"""Review output must survive parsing, persistence and the next role prompt."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from opc.core.models import DelegationWorkItem, Phase, Task, TaskResult, TaskStatus
from opc.core.review_verdict import parse_review_verdict, review_feedback_error
from opc.database.store import OPCStore
from opc.layer1_perception.context_assembler import ContextAssembler
from opc.layer2_organization.company_mode import review_work_item_id_for_attempt
from opc.layer2_organization.work_item_links import set_linked_work_item_id
from opc.layer2_organization.org_work_item_planner import (
    CompanyWorkItemRuntimePlan, WorkItemGatePolicy, WorkItemProjectionSpec,
)
from opc.layer2_organization.company_mode import CompanyWorkItemExecutor
from opc.layer3_agent.native_agent import NativeAgent
from tests import test_fix4_and_fix6 as adapter_tests
from tests import test_company_collaboration as collaboration_tests
from tests.test_verdict_parse_retry import (
    _build_executor, _build_review_setup, _make_org_engine,
    _make_review_card, _make_review_task,
)


REVIEW = {
    "review_verdict": "reject",
    "summary": "报告使用了过期输入，需重新核实来源。",
    "blocking_issues": ["核实最新来源并标注 URL", "重新校准结论", "更新数据截止时间", "修正置信度声明"],
    "followups": ["解释来源分歧", "补充时间线", "说明合成方法"],
}


class ReviewParserTests(unittest.TestCase):
    def test_native_and_external_accept_identical_shapes_without_losing_fields(self):
        adapter = adapter_tests.InferReviewVerdictAdapterIntegrationTests._StubAdapter()
        expected = {"label": "reject", **{k: v for k, v in REVIEW.items() if k != "review_verdict"}}
        shapes = [
            REVIEW, {"review_verdict": REVIEW}, {"structured_review_verdict": expected},
            {**{k: v for k, v in REVIEW.items() if k != "review_verdict"}, "verdict": "reject"},
            expected,
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                raw = 'Unrelated data: {"status":"running"}\n```json\n' + json.dumps(shape, ensure_ascii=False) + '\n```'
                self.assertEqual(parse_review_verdict(raw), expected)
                self.assertEqual(adapter.infer_review_verdict(raw), expected)

    def test_same_decision_repairs_legacy_artifacts_but_conflicts_do_not(self):
        raw = json.dumps(REVIEW)
        legacy = {"review_verdict": {"label": "reject", "summary": "reject"}}
        self.assertEqual(parse_review_verdict(raw, legacy)["blocking_issues"], REVIEW["blocking_issues"])
        approved = {"review_verdict": {"label": "approve", "summary": "Accepted independently."}}
        self.assertEqual(parse_review_verdict(raw, approved)["label"], "approve")
        self.assertEqual(parse_review_verdict('{"review_verdict":"approve"}', legacy)["label"], "reject")

    def test_missing_feedback_validation_is_not_a_length_or_language_test(self):
        for value in [
            {"review_verdict": "reject"},
            {"review_verdict": "reject", "summary": " **REJECT.** ", "blocking_issues": [" "]},
            {"review_verdict": "reject", "summary": "驳回", "followups": ["Nice to have"]},
            {"review_verdict": "reject", "summary": None, "blocking_issues": [None, True, {}]},
        ]:
            with self.subTest(value=value):
                self.assertIn("REVIEW_REJECT_FEEDBACK_MISSING", review_feedback_error(parse_review_verdict(json.dumps(value))))
        for value in [
            {"review_verdict": "reject", "summary": "缺测试"},
            {"review_verdict": "reject", "blocking_issues": ["Fix X"]},
            {"review_verdict": "approve"},
        ]:
            self.assertEqual(review_feedback_error(parse_review_verdict(json.dumps(value))), "")

    def test_all_blocking_issues_survive(self):
        value = {**REVIEW, "blocking_issues": [f"Fix item {n}" for n in range(12)]}
        self.assertEqual(parse_review_verdict(json.dumps(value))["blocking_issues"], value["blocking_issues"])


class ReviewFeedbackLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = OPCStore(self.root / "tasks.db")
        await self.store.initialize()
        self.org = _make_org_engine(self.root)
        self.executor = _build_executor(self.store, self.org)
        self.child, self.review_id = _build_review_setup(self.store)
        self.report_id = "report::wi-child::v1"
        report = DelegationWorkItem(
            work_item_id=self.report_id, run_id="run-1", cell_id="team::cto",
            role_id="engineer", parent_work_item_id="wi-child", kind="report", phase=Phase.APPROVED,
            metadata={"report_target_work_item_id": "wi-child", "report_attempt": 1,
                      "report_card_outcome": "applied", "completion_report": "Worker submission"},
        )
        await self.store.save_delegation_work_item(self.child)
        await self.store.save_delegation_work_item(report)
        card = _make_review_card(review_card_id=self.review_id)
        card.metadata["review_source_report_work_item_id"] = self.report_id
        await self.store.save_delegation_work_item(card)

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    async def finish(self, content, *, attempt=1, legacy=False):
        rid = review_work_item_id_for_attempt("wi-child", attempt)
        card = await self.store.get_delegation_work_item(rid)
        if card.phase == Phase.READY:
            await self.store.update_delegation_work_item(rid, phase=Phase.RUNNING)
        task = _make_review_task(review_card_id=rid, structured_verdict={})
        task.id = f"review-task-{attempt}"
        task.metadata.update(card.metadata)
        task.metadata["review_attempt"] = attempt
        task.result = {"content": content, "artifacts": {}}
        result = TaskResult(status=TaskStatus.DONE, content=content, artifacts={})
        if legacy:
            task.metadata["structured_review_verdict"] = {"label": "reject", "summary": "reject"}
        else:
            bundle = self.executor._capture_work_item_outputs(task, result)
            await self.executor._persist_work_item_owned_output_metadata(task, bundle)
        await self.store.save_task(task)
        await self.store.link_work_item_runtime_task(rid, task.id)
        task = await self.store.get_task(task.id)
        await self.executor._finalize_review_work_item(task)
        return task

    async def test_native_raw_output_survives_store_reload_into_worker_prompt(self):
        await self.finish(json.dumps(REVIEW, ensure_ascii=False))
        child = await self.store.get_delegation_work_item("wi-child")
        self.assertEqual(child.phase, Phase.READY_FOR_REWORK)
        self.assertEqual(child.metadata["structured_review_verdict"]["blocking_issues"], REVIEW["blocking_issues"])
        worker = Task(id="worker-reloaded", title="Build", assigned_to="engineer", metadata={"runtime_model": "multi_team_org"})
        set_linked_work_item_id(worker, "wi-child")
        prompt = await ContextAssembler(memory=MagicMock(), store=self.store).build_rework_feedback_context(worker)
        for text in [REVIEW["summary"], *REVIEW["blocking_issues"], *REVIEW["followups"]]:
            self.assertIn(text, prompt)

    async def test_legacy_label_only_metadata_recovers_from_same_raw_review(self):
        await self.finish(json.dumps(REVIEW), legacy=True)
        child = await self.store.get_delegation_work_item("wi-child")
        self.assertEqual(child.phase, Phase.READY_FOR_REWORK)
        self.assertIn(REVIEW["blocking_issues"][0], child.metadata["rework_feedback"])

    async def test_missing_feedback_retries_same_reviewer_and_hint_is_in_actual_context(self):
        await self.finish('{"review_verdict":"reject"}')
        child = await self.store.get_delegation_work_item("wi-child")
        self.assertEqual(child.phase, Phase.AWAITING_MANAGER_REVIEW)
        self.assertNotIn("rework_feedback", child.metadata)
        self.assertNotIn("review_rework_count", child.metadata)
        self.assertNotIn("review_verdict_parse_retry_count", child.metadata)
        retry = await self.store.get_delegation_work_item(review_work_item_id_for_attempt("wi-child", 2))
        self.assertEqual((retry.role_id, retry.seat_id), ("cto", "seat::team::cto::cto"))
        task = _make_review_task(review_card_id=retry.work_item_id, structured_verdict={})
        task.metadata.pop("review_retry_hint", None)  # must load durable card
        prompt = await ContextAssembler(memory=MagicMock(), store=self.store).build_turn_mode_context(task)
        self.assertIn("REVIEW_REJECT_FEEDBACK_MISSING", prompt)
        self.assertIn("this error belongs to you, the reviewer", prompt)
        self.assertIn("blocking_issues", prompt)
        native = NativeAgent.__new__(NativeAgent)
        native.context_assembler = ContextAssembler(memory=MagicMock(), store=self.store)
        task.metadata.update(retry.metadata)
        correction = await native._build_user_message(task)
        self.assertIn("REVIEW_REJECT_FEEDBACK_MISSING", correction)
        self.assertIn("Regenerate your review verdict", correction)
        self.assertTrue(task.metadata["_runtime_v2_attempt_user_seed_required"])
        self.assertEqual(await native._build_user_message(task), correction)
        await self.finish(json.dumps(REVIEW), attempt=2)
        child = await self.store.get_delegation_work_item("wi-child")
        self.assertEqual(child.phase, Phase.READY_FOR_REWORK)
        self.assertEqual(child.metadata["review_rework_count"], 1)

    async def test_exhaustion_after_restart_fails_reviewer_without_auto_approving_child(self):
        for attempt in (1, 2, 3):
            # New executor each time: no process-local counter may reset budget.
            self.executor = _build_executor(self.store, self.org)
            # Malformed response during feedback correction must not fall into
            # the old unparseable-verdict auto-approval path.
            content = 'No JSON this time' if attempt == 3 else '{"review_verdict":"reject"}'
            task = await self.finish(content, attempt=attempt)
        child = await self.store.get_delegation_work_item("wi-child")
        self.assertEqual(child.phase, Phase.AWAITING_MANAGER_REVIEW)
        self.assertNotIn("review_rework_count", child.metadata)
        self.assertNotIn("review_verdict_parse_failed_auto_done", child.metadata)
        review = await self.store.get_delegation_work_item(review_work_item_id_for_attempt("wi-child", 3))
        self.assertEqual(review.phase, Phase.FAILED)
        self.assertEqual(review.metadata["review_work_item_outcome"], "reject_feedback_retry_exhausted")
        self.assertFalse(review.metadata["hidden_from_company_kanban"])
        self.assertIsNone(await self.store.get_delegation_work_item(review_work_item_id_for_attempt("wi-child", 4)))
        await self.executor._reconcile_missing_review_chain(await self.store.list_delegation_work_items("run-1"))
        self.assertIsNone(await self.store.get_delegation_work_item(review_work_item_id_for_attempt("wi-child", 4)))
        # Re-finalizing the failed card is idempotent and cannot auto-approve.
        await self.executor._finalize_review_work_item(task)
        self.assertEqual((await self.store.get_delegation_work_item("wi-child")).phase, Phase.AWAITING_MANAGER_REVIEW)


    async def test_completed_review_does_not_leak_retry_hint_to_next_review(self):
        task = _make_review_task(review_card_id=self.review_id, structured_verdict={})
        task.metadata["review_retry_hint"] = "STALE OTHER ATTEMPT"
        prompt = await ContextAssembler(memory=MagicMock(), store=self.store).build_turn_mode_context(task)
        self.assertNotIn("STALE OTHER ATTEMPT", prompt)

    async def test_stale_review_cannot_touch_worker_or_spawn_retry(self):
        await self.store.update_delegation_work_item("wi-child", phase=Phase.READY_FOR_REWORK)
        await self.store.update_delegation_work_item("wi-child", phase=Phase.RUNNING)
        await self.finish('{"review_verdict":"reject"}')
        self.assertEqual((await self.store.get_delegation_work_item("wi-child")).phase, Phase.RUNNING)
        self.assertIsNone(await self.store.get_delegation_work_item(review_work_item_id_for_attempt("wi-child", 2)))

    async def test_retry_insert_failure_recovers_error_and_owner_without_duplicate_cards(self):
        insert = self.store.insert_delegation_work_item_if_absent
        retry_id = review_work_item_id_for_attempt("wi-child", 2)

        async def fail_retry(item):
            if item.work_item_id == retry_id:
                raise RuntimeError("injected retry card insert failure")
            return await insert(item)

        with patch.object(self.store, "insert_delegation_work_item_if_absent", side_effect=fail_retry):
            await self.finish('{"review_verdict":"reject"}')
        self.assertIsNone(await self.store.get_delegation_work_item(retry_id))
        self.executor = _build_executor(self.store, self.org)
        for _ in range(2):
            await self.executor._reconcile_missing_review_chain(await self.store.list_delegation_work_items("run-1"))
        retry = await self.store.get_delegation_work_item(retry_id)
        self.assertEqual(retry.role_id, "cto")
        self.assertEqual(retry.metadata["review_retry_reason"], "reject_feedback_missing")
        self.assertIn("REVIEW_REJECT_FEEDBACK_MISSING", retry.metadata["review_retry_hint"])
        self.assertIsNone(await self.store.get_delegation_work_item(review_work_item_id_for_attempt("wi-child", 3)))
        self.assertEqual((await self.store.get_delegation_work_item("wi-child")).phase, Phase.AWAITING_MANAGER_REVIEW)

    async def test_legacy_gate_retries_reviewer_without_calling_worker_rework(self):
        task = Task(
            id="legacy-review", title="Review", description="Review worker output", assigned_to="cto",
            status=TaskStatus.RUNNING,
            metadata={"work_item_turn_type": "review", "review_task": True},
            result={"content": '{"review_verdict":"reject"}'},
        )
        gate = WorkItemGatePolicy(gate_type="review", on_reject="rework")
        self.executor.prepare_gate_rework = AsyncMock()
        native = NativeAgent.__new__(NativeAgent)
        native.context_assembler = ContextAssembler(memory=MagicMock(), store=self.store)
        for count in (1, 2):
            await self.executor._apply_gate(task, gate, {})
            self.assertTrue(task.metadata.pop("_retry_contract_enforcement"))
            self.assertEqual(task.context_snapshot["review_output_retry"]["count"], count)
            correction = await native._build_user_message(task)
            self.assertIn("REVIEW_REJECT_FEEDBACK_MISSING", correction)
            self.assertIn(f"Retry {count}", correction)
        await self.executor._apply_gate(task, gate, {})
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.executor.prepare_gate_rework.assert_not_awaited()
        self.assertEqual((await self.store.get_delegation_work_item("wi-child")).phase, Phase.AWAITING_MANAGER_REVIEW)

class ReviewGateExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_loop_regenerates_review_before_applying_decision(self):
        calls = []

        async def execute(task):
            calls.append(task.assigned_to)
            if len(calls) == 1:
                content = '{"review_verdict":"reject"}'
            else:
                self.assertEqual(task.context_snapshot["review_output_retry"]["count"], 1)
                content = json.dumps(REVIEW)
            result = TaskResult(status=TaskStatus.DONE, content=content)
            task.status = result.status
            task.result = {"content": content, "artifacts": {}}
            return result

        executor = CompanyWorkItemExecutor(
            org_engine=collaboration_tests.DummyOrgEngine(),
            communication=SimpleNamespace(refresh_waiting_tasks=AsyncMock(return_value=[]), detect_deadlocks=AsyncMock(return_value=[])),
            approval_engine=MagicMock(), memory=collaboration_tests.DummyMemory(),
            execute_task=execute, save_task=AsyncMock(),
        )
        task = Task(
            id="review-gate-loop", title="Review", project_id="proj1", assigned_to="reviewer",
            status=TaskStatus.PENDING,
            metadata={
                "execution_mode": "company_mode", "work_item_projection_id": "qa_review",
                "work_item_gate": {"type": "review", "on_reject": "halt", "metadata": {}},
                "runtime_policy": {"review": {"enable_work_item_gates": True}},
                "work_item_turn_type": "review", "work_item_verification_required": False,
            },
        )
        plan = CompanyWorkItemRuntimePlan(
            profile="corporate",
            projections=[WorkItemProjectionSpec(
                projection_id="qa_review", turn_type="review", title="Review", summary="Review the output",
                role_id="reviewer", gate_policy=WorkItemGatePolicy(gate_type="review", on_reject="halt"),
            )],
            metadata={"runtime_policy": {"review": {"enable_work_item_gates": True}}},
        )
        await executor.execute(plan, [task])
        self.assertEqual(calls, ["reviewer", "reviewer"])
        self.assertEqual(task.metadata["structured_review_verdict"]["blocking_issues"], REVIEW["blocking_issues"])

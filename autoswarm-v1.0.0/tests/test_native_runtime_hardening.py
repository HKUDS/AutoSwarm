"""Execution boundaries and context invariants for the Talen backports."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from opc.core.config import OPCConfig
from opc.core.models import Task, TaskResult, TaskStatus
from opc.core.session_parts import normalized_session_parts
from opc.layer3_agent.runtime_v2.runtime import NativeRuntimeV2, _RuntimePrefetchHandle
from opc.layer3_agent.runtime_v2.stream_events import RuntimeDeltaBuffer
from opc.layer4_tools.registry import ToolRegistry


def runtime(**kwargs):
    cfg = OPCConfig()
    cfg.system.native_runtime.prompt_harness.enabled = False
    return NativeRuntimeV2(llm=SimpleNamespace(config=SimpleNamespace(max_tokens=1024),
        prepare_user_message_content=lambda content, **kwargs: content,
        get_tool_definitions=lambda tools: tools), tool_registry=ToolRegistry(), config=cfg, **kwargs)


def test_budget_preserves_endpoints_and_is_idempotent_even_at_small_limits():
    rt = runtime()
    for budget in (1, 50, 12000):
        rt.config.system.native_runtime.tool_result_budget_chars = budget
        messages = [{"role": "tool", "tool_call_id": "a", "content": "HEAD" + "x" * 25000 + "TAIL"}]
        once = rt._apply_tool_result_budget(messages)
        assert len(once[0]["content"]) <= budget
        assert once == rt._apply_tool_result_budget(once)
        if budget == 12000:
            assert once[0]["content"].startswith("HEAD")
            assert once[0]["content"].endswith("TAIL")


def test_resume_detection_remains_explicit():
    task = Task(title="retry", metadata={"runtime_v2": {"runtime_session_id": "rt"}})
    assert runtime()._runtime_resume_payload(task) == {}
    task.context_snapshot["runtime_resume"] = {"runtime_session_id": "rt"}
    assert runtime()._runtime_resume_payload(task)["runtime_session_id"] == "rt"


def test_noop_compaction_does_not_consume_real_failure_budget():
    async def run():
        summarize = AsyncMock(side_effect=RuntimeError("summary unavailable"))
        rt = runtime(history_compactor=SimpleNamespace(summarize_runtime_history=summarize))
        notes = {}
        params = dict(tool_schemas=[], task=None, base_prefix_len=1, runtime_session_id="rt",
                      compaction_boundaries=[], runtime_notes=notes, todo_state=[],
                      active_subagents=[], force_compact=True)
        for _ in range(3):
            await rt._apply_context_pipeline([{"role": "system", "content": "s"}], **params)
        summarize.assert_not_awaited()
        assert notes.get("durable_compaction_failures", 0) == 0
        messages = [{"role": "system", "content": "s"}] + [{"role": "assistant", "content": str(i)} for i in range(20)]
        for _ in range(3):
            await rt._apply_context_pipeline(messages, **params)
        assert summarize.await_count == 2
        assert notes["durable_compaction_failures"] == 2
    asyncio.run(run())


def test_sparse_stream_index_and_reordering_reuse_each_completed_call():
    async def run():
        rt = runtime()
        calls = [{"id": f"call-{i}", "function": "file_read", "arguments": {"path": str(i)}} for i in (4, 9)]
        async def execute(calls, **kwargs):
            return [{"tool_call": call, "result": {"success": True}} for call in calls]
        execute = AsyncMock(side_effect=execute)
        early = {i: {"call": call, "task": asyncio.create_task(execute([call]))} for i, call in zip((4, 9), calls)}
        result = await rt._collect_execution_results(tool_calls=list(reversed(calls)), early_tool_runs=early,
            executor=SimpleNamespace(execute=execute), task=None, on_progress=None)
        assert execute.await_count == 2
        assert [item["tool_call"]["id"] for item in result] == ["call-9", "call-4"]
    asyncio.run(run())


def test_failure_envelope_is_saved_and_contains_latest_state():
    async def run():
        task = Task(title="iteration limit", session_id="session")
        rt = runtime(max_iterations=0)
        result = await rt.run("system", "user", task=task)
        for key in ("resume_state", "resume_cursor", "task_ledger", "artifact_manifest",
                    "compaction_records", "active_subagents", "worktree_path", "permission_requests"):
            assert key in result.artifacts
            assert task.metadata["runtime_v2"][key] == result.artifacts[key]
    asyncio.run(run())


def test_legacy_pairing_preserves_error_envelope_and_unmatched_results():
    def part(kind, payload):
        return SimpleNamespace(part_type=kind, payload=payload)
    parts = [part("tool_output", {"output": '{"success":false,"result":{"body":"a"},"error":"denied"}'}),
             part("tool_output", {"tool_call_id": "other", "output": "separate result"}),
             part("tool_result", {"tool_call_id": "call", "result": {"body": "a"}})]
    result = normalized_session_parts(parts)
    assert len(result) == 2
    assert result[0][1]["result"] == "separate result"
    assert result[1][1]["result"]["error"] == "denied"
    assert result[1][1]["result"]["success"] is False
    # Identical payloads for different calls must remain distinct.
    assert len(normalized_session_parts([part("tool_output", {"tool_call_id": "a", "output": "x"}),
        part("tool_result", {"tool_call_id": "b", "result": "x"})])) == 2
    assert len(normalized_session_parts([part("tool_output", {"output": "x"}),
        part("tool_output", {"output": "x"})])) == 2


def test_dynamic_tail_keeps_company_policy_and_pending_tool_boundary():
    async def run():
        rt = runtime()
        rt.config.system.native_runtime.prompt_harness.enabled = True
        prefix = [{"role": "system", "content": "company policy"}, {"role": "user", "content": "original request"}]
        pending = {"role": "assistant", "content": "", "tool_calls": [{"id": "approved"}]}
        dynamic = [{"role": "system", "content": "## Runtime Artifact: State\nvolatile"}]
        assert rt._append_dynamic_context([*prefix, pending], dynamic) == [*prefix, *dynamic, pending]
        messages, count = await rt._bootstrap_messages(system_prompt="company policy", user_content="original request",
            user_message="original request", context_messages=dynamic, task=None)
        assert count == 2
        assert messages[:count] == prefix
        for value in ("first", "second"):
            handle = _RuntimePrefetchHandle(task=asyncio.create_task(asyncio.sleep(0, result={"focused_memory": value})), query=value)
            await handle.task
            messages, new_count, _ = await rt._consume_ready_prefetch(handle, messages=messages, base_prefix_len=count,
                runtime_session_id="rt", task=None)
            assert new_count == count and messages[:count] == prefix
        assert sum("## Runtime Prefetch" in item["content"] for item in messages) == 1
        assert "second" in messages[-1]["content"]
    asyncio.run(run())


def test_delta_buffer_preserves_content_ids_sequence_and_boundary_order():
    async def run():
        emitted = []
        async def emit(payload):
            emitted.append(payload)
        buffer = RuntimeDeltaBuffer(emit, delay_ms=1000, max_chars=512)
        for i in range(100):
            await buffer.push({"type": "thinking_delta", "stream_id": "turn:iter:1:thinking",
                               "runtime_session_id": "rt", "text": "字", "seq": i + 1, "timestamp_ms": i})
        await buffer.push({"type": "tool_started", "tool_call_id": "approved"})
        await buffer.push({"type": "thinking_delta", "stream_id": "turn:iter:2:thinking", "text": "next", "seq": 1})
        await buffer.close()
        assert [item["type"] for item in emitted] == ["thinking_delta", "tool_started", "thinking_delta"]
        assert emitted[0]["text"] == "字" * 100
        assert emitted[0]["seq"] == 100 and emitted[0]["timestamp_ms"] == 0
        assert emitted[2]["seq"] == 1
        assert buffer._timer is None
    asyncio.run(run())


def test_delta_timer_flush_and_failures_are_observed():
    async def run():
        received = asyncio.Event()
        async def emit(payload):
            received.set()
        buffer = RuntimeDeltaBuffer(emit, delay_ms=1, max_chars=512)
        await buffer.push({"type": "assistant_delta", "stream_id": "s", "text": "hi"})
        await asyncio.wait_for(received.wait(), 1)
        await buffer.close()
        assert buffer._timer is None
        broken = RuntimeDeltaBuffer(AsyncMock(side_effect=ValueError("write failed")), delay_ms=1, max_chars=512)
        await broken.push({"type": "assistant_delta", "stream_id": "s", "text": "hi"})
        await asyncio.sleep(.01)
        try:
            await broken.close()
        except ValueError as exc:
            assert str(exc) == "write failed"
        else:
            raise AssertionError("Lost background write failure")
    asyncio.run(run())


def test_parallel_invocations_do_not_share_delta_buffers():
    async def run():
        rt = runtime()
        rt.config.system.native_runtime.delta_coalesce_ms = 1000
        received = []
        async def write(payload):
            received.append(payload)
        rt._write_runtime_event = write
        async def impl(system_prompt, user_message, **kwargs):
            for seq in (1, 2):
                await rt._emit_runtime_event(user_message, None, "assistant_delta", {
                    "stream_id": "same-stream", "text": user_message, "seq": seq,
                })
                await asyncio.sleep(0)
            return TaskResult(status=TaskStatus.DONE, content=user_message)
        rt._run_impl = impl
        await asyncio.gather(rt.run("s", "a"), rt.run("s", "b"))
        assert sorted((item["runtime_session_id"], item["text"]) for item in received) == [("a", "aa"), ("b", "bb")]
        assert rt._delta_buffer.get() is None
    asyncio.run(run())


def test_cancellation_flushes_text_and_joins_permission_cleanup():
    async def run():
        rt = runtime()
        started = asyncio.Event()
        rt._settle_aborted_runtime = AsyncMock()
        rt._write_runtime_event = AsyncMock()
        async def impl(*args, **kwargs):
            await rt._emit_runtime_event("rt", kwargs["task"], "thinking_delta", {
                "stream_id": "turn:thinking", "text": "partial", "seq": 1,
            })
            started.set()
            await asyncio.Event().wait()
        rt._run_impl = impl
        task = Task(title="cancel")
        worker = asyncio.create_task(rt.run("s", "u", task=task))
        await started.wait()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation was swallowed")
        rt._settle_aborted_runtime.assert_awaited_once()
        rt._write_runtime_event.assert_awaited_once()
        assert rt._write_runtime_event.call_args.args[0]["text"] == "partial"
    asyncio.run(run())

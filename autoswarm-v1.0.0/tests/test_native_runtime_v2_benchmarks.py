from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

NATIVE_RUNTIME_V2_SUITE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "native_runtime_v2_suite.json"
)

REQUIRED_NATIVE_RUNTIME_V2_SCENARIOS = (
    "long_session_coding",
    "repo_search_implement",
    "browser_verification",
    "permission_intercept",
    "subagent_parallel",
    "compaction_continue",
    "checkpoint_resume",
    "company_work_item_execution",
)

REQUIRED_NATIVE_RUNTIME_V2_METRICS = (
    "success_rate",
    "average_turns",
    "tool_success_rate",
    "permission_interruption_rate",
    "token_cost_usd",
    "resume_success_rate",
    "cache_stable_prefix_reuse_rate",
    "verifier_catch_rate",
)


def load_native_runtime_v2_suite(path: Path | None = None) -> dict[str, Any]:
    suite_path = path or NATIVE_RUNTIME_V2_SUITE_PATH
    with open(suite_path, encoding="utf-8") as f:
        return json.load(f)


def validate_native_runtime_v2_suite(suite: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    scenarios = list(suite.get("scenarios", []) or [])
    scenario_ids = {str(item.get("scenario_id", "")).strip() for item in scenarios}
    missing_scenarios = [
        item for item in REQUIRED_NATIVE_RUNTIME_V2_SCENARIOS if item not in scenario_ids
    ]
    if missing_scenarios:
        errors.append(f"Missing benchmark scenarios: {', '.join(missing_scenarios)}")

    metrics = list(suite.get("metrics", []) or [])
    missing_metrics = [item for item in REQUIRED_NATIVE_RUNTIME_V2_METRICS if item not in metrics]
    if missing_metrics:
        errors.append(f"Missing benchmark metrics: {', '.join(missing_metrics)}")

    for scenario in scenarios:
        scenario_id = str(scenario.get("scenario_id", "") or "").strip()
        if not scenario_id:
            errors.append("Scenario missing scenario_id")
            continue
        if not str(scenario.get("name", "") or "").strip():
            errors.append(f"Scenario `{scenario_id}` missing name")
        if not str(scenario.get("goal", "") or "").strip():
            errors.append(f"Scenario `{scenario_id}` missing goal")
        acceptance = list(scenario.get("acceptance_signals", []) or [])
        if not acceptance:
            errors.append(f"Scenario `{scenario_id}` missing acceptance_signals")
    return errors


def summarize_native_runtime_v2_runs(runs: list[dict[str, Any]]) -> dict[str, float]:
    if not runs:
        return {metric: 0.0 for metric in REQUIRED_NATIVE_RUNTIME_V2_METRICS}

    total = float(len(runs))
    success_count = sum(1 for run in runs if run.get("success"))
    average_turns = sum(float(run.get("turns", 0) or 0) for run in runs) / total
    tool_success_rate = (
        sum(float(run.get("tool_success_rate", 0) or 0) for run in runs) / total
    )
    permission_interruption_rate = (
        sum(float(run.get("permission_interruption_rate", 0) or 0) for run in runs) / total
    )
    token_cost_usd = sum(float(run.get("token_cost_usd", 0) or 0) for run in runs)
    resume_runs = [run for run in runs if run.get("checkpoint_resume_attempted")]
    resume_success_rate = (
        sum(1 for run in resume_runs if run.get("resume_success")) / float(len(resume_runs))
        if resume_runs
        else 0.0
    )
    cache_stable_prefix_reuse_rate = (
        sum(float(run.get("cache_stable_prefix_reuse_rate", 0) or 0) for run in runs) / total
    )
    verifier_catch_rate = (
        sum(float(run.get("verifier_catch_rate", 0) or 0) for run in runs) / total
    )
    return {
        "success_rate": success_count / total,
        "average_turns": average_turns,
        "tool_success_rate": tool_success_rate,
        "permission_interruption_rate": permission_interruption_rate,
        "token_cost_usd": token_cost_usd,
        "resume_success_rate": resume_success_rate,
        "cache_stable_prefix_reuse_rate": cache_stable_prefix_reuse_rate,
        "verifier_catch_rate": verifier_catch_rate,
    }


class NativeRuntimeV2BenchmarkSuiteTests(unittest.TestCase):
    def test_benchmark_suite_contains_required_scenarios_and_metrics(self) -> None:
        suite = load_native_runtime_v2_suite()
        errors = validate_native_runtime_v2_suite(suite)
        self.assertEqual(errors, [])

    def test_benchmark_summary_reports_required_metrics(self) -> None:
        summary = summarize_native_runtime_v2_runs(
            [
                {
                    "success": True,
                    "turns": 12,
                    "tool_success_rate": 1.0,
                    "permission_interruption_rate": 0.25,
                    "token_cost_usd": 1.5,
                    "checkpoint_resume_attempted": True,
                    "resume_success": True,
                    "cache_stable_prefix_reuse_rate": 0.8,
                    "verifier_catch_rate": 0.2,
                },
                {
                    "success": False,
                    "turns": 18,
                    "tool_success_rate": 0.5,
                    "permission_interruption_rate": 0.5,
                    "token_cost_usd": 2.0,
                    "checkpoint_resume_attempted": True,
                    "resume_success": False,
                    "cache_stable_prefix_reuse_rate": 0.4,
                    "verifier_catch_rate": 0.6,
                },
            ]
        )
        self.assertAlmostEqual(summary["success_rate"], 0.5)
        self.assertAlmostEqual(summary["average_turns"], 15.0)
        self.assertAlmostEqual(summary["tool_success_rate"], 0.75)
        self.assertAlmostEqual(summary["permission_interruption_rate"], 0.375)
        self.assertAlmostEqual(summary["token_cost_usd"], 3.5)
        self.assertAlmostEqual(summary["resume_success_rate"], 0.5)
        self.assertAlmostEqual(summary["cache_stable_prefix_reuse_rate"], 0.6)
        self.assertAlmostEqual(summary["verifier_catch_rate"], 0.4)


if __name__ == "__main__":
    unittest.main()

"""Long-lived counters must survive overlapping windows, retries and removals."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("collect_downloads", Path(__file__).resolve().parents[1] / "scripts/collect_downloads.py")
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


def snapshot(day="2026-10-24", rows=None, assets=None):
    return {"repository": "HKUDS/AutoSwarm", "observed_at": day + "T12:00:00+00:00",
            "clones": {"clones": [{"timestamp": d + "T00:00:00Z", "count": n, "uniques": u} for d, n, u in (rows or [])]},
            "releases": [{"id": 1, "tag": "v2", "assets": assets or []}]}


def asset(identity, downloads, created="2026-10-24"):
    return {"id": identity, "name": "通用 wheel.whl", "download_count": downloads, "created_at": created + "T01:00:00Z"}


class DownloadStatisticsTests(unittest.TestCase):
    def test_baseline_retains_old_rows_but_only_counts_from_start_date(self):
        history = stats.merge_history(None, snapshot(rows=[("2026-10-23", 100, 70), ("2026-10-24", 3, 2)]), "2026-10-24")
        self.assertIn("2026-10-23", history["clones"])
        self.assertEqual(stats.totals(history)["clones"], 3)
        self.assertEqual(history["clones"]["2026-10-24"]["uniques"], 2)
        self.assertNotIn("unique_users", stats.totals(history))

    def test_repeated_overlapping_windows_and_corrections_do_not_double_count(self):
        history = stats.merge_history(None, snapshot(rows=[("2026-10-24", 5, 3)]))
        update = snapshot("2026-10-25", [("2026-10-24", 4, 3), ("2026-10-25", 7, 2)])
        history = stats.merge_history(history, update)
        again = stats.merge_history(history, update)
        self.assertEqual(history, again)
        self.assertEqual(stats.totals(again)["clones"], 11)

    def test_rows_outside_a_later_api_window_are_preserved(self):
        history = stats.merge_history(None, snapshot(rows=[("2026-10-24", 8, 4)]))
        history = stats.merge_history(history, snapshot("2026-11-20", [("2026-11-20", 2, 1)]))
        self.assertEqual(stats.totals(history)["clones"], 10)
        self.assertIn("2026-10-25", stats.totals(history)["missing_clone_dates"])
        badge = json.loads(stats.outputs(history)["clones-badge.json"])
        self.assertEqual(badge["message"], "10+")
        self.assertEqual(badge["color"], "orange")

    def test_unreported_days_are_pending_and_explicit_zero_is_known(self):
        history = stats.merge_history(None, snapshot(rows=[("2026-10-23", 100, 70)]), "2026-10-24")
        self.assertIsNone(stats.totals(history)["clones"])
        self.assertEqual(stats.totals(history)["latest_clone_day"], "2026-10-23")
        self.assertEqual(json.loads(stats.outputs(history)["clones-badge.json"])["message"], "pending")
        history = stats.merge_history(history, snapshot(rows=[("2026-10-24", 0, 0)]))
        self.assertEqual(stats.totals(history)["clones"], 0)

    def test_deleted_assets_and_reuploads_keep_their_recorded_downloads(self):
        history = stats.merge_history(None, snapshot(assets=[asset(1, 100, "2026-10-01"), asset(2, 3)]))
        self.assertEqual(stats.totals(history)["release_downloads"], 3)
        history = stats.merge_history(history, snapshot("2026-10-25", assets=[asset(1, 104, "2026-10-01"), asset(2, 5)]))
        self.assertEqual(stats.totals(history)["release_downloads"], 9)
        history = stats.merge_history(history, snapshot("2026-10-26"))
        self.assertFalse(history["release_assets"]["1"]["present"])
        self.assertEqual(stats.totals(history)["release_downloads"], 9)
        history = stats.merge_history(history, snapshot("2026-10-27", assets=[asset(3, 2, "2026-10-27")]))
        self.assertEqual(stats.totals(history)["release_downloads"], 11)

    def test_asset_counter_corrections_use_the_original_baseline(self):
        history = stats.merge_history(None, snapshot(assets=[asset(1, 100, "2026-10-01")]))
        history = stats.merge_history(history, snapshot("2026-10-25", assets=[asset(1, 110, "2026-10-01")]))
        history = stats.merge_history(history, snapshot("2026-10-26", assets=[asset(1, 108, "2026-10-01")]))
        self.assertEqual(stats.totals(history)["release_downloads"], 8)

    def test_mismatched_repository_start_date_and_stale_snapshot_fail_closed(self):
        history = stats.merge_history(None, snapshot(rows=[("2026-10-24", 5, 2)]))
        original = copy.deepcopy(history)
        bad = snapshot(); bad["repository"] = "someone/else"
        for candidate, since in [(bad, None), (snapshot(), "2026-10-23"), (snapshot("2026-10-23"), None)]:
            with self.subTest(snapshot=candidate, since=since), self.assertRaises(ValueError):
                stats.merge_history(history, candidate, since)
        self.assertEqual(history, original)

    def test_invalid_counters_and_naive_timestamps_do_not_mutate_history(self):
        history = stats.merge_history(None, snapshot())
        original = copy.deepcopy(history)
        for value in [-1, True, "12"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                stats.merge_history(history, snapshot(rows=[("2026-10-24", value, 0)]))
        bad = snapshot(); bad["observed_at"] = "2026-10-24T12:00:00"
        with self.assertRaises(ValueError): stats.merge_history(history, bad)
        self.assertEqual(history, original)

    def test_fetch_paginates_asset_lists_and_excludes_draft_releases(self):
        urls = []
        def api(path, token):
            urls.append(path)
            if "/traffic/" in path: return {"clones": []}
            if "/assets?" in path:
                return [asset(n, 1) for n in range(100)] if path.endswith("&page=1") else [asset(101, 2)]
            return [{"id": 1, "draft": False, "tag_name": "v2"}, {"id": 2, "draft": True, "tag_name": "draft"}]
        with patch.object(stats, "request_json", side_effect=api):
            result = stats.fetch_snapshot("HKUDS/AutoSwarm", "test-only-token")
        self.assertEqual(len(result["releases"]), 1)
        self.assertEqual(len(result["releases"][0]["assets"]), 101)
        self.assertTrue(any("assets?per_page=100&page=2" in url for url in urls))
        self.assertFalse(any("/releases/2/" in url for url in urls))

    def test_pagination_does_not_stop_after_the_first_release_page(self):
        with patch.object(stats, "request_json", side_effect=[list(range(100)), [100, 101]]):
            self.assertEqual(list(stats.pages("/repos/HKUDS/AutoSwarm/releases", "test-only-token")), list(range(102)))

    def test_output_roundtrip_has_no_extra_state_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            history = stats.merge_history(None, snapshot(rows=[("2026-10-24", 12, 3)], assets=[asset(2, 4)]))
            stats.write_outputs(history, directory)
            self.assertEqual(set(p.name for p in directory.iterdir()), set(stats.FILES))
            self.assertEqual(json.loads((directory / "history.json").read_text(encoding="utf-8")), history)
            self.assertEqual(json.loads((directory / "release-downloads-badge.json").read_text())["message"], "4")

    def test_failed_api_or_bad_history_never_replaces_saved_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            stats.write_outputs(stats.merge_history(None, snapshot()), directory)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            with patch.dict(os.environ, {"TRAFFIC_TOKEN": "test-only-token"}), patch.object(stats, "fetch_snapshot", side_effect=RuntimeError("API unavailable")), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                stats.main(["--repository", "HKUDS/AutoSwarm", "--output", str(directory)])
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    def test_missing_secret_and_invalid_repository_fail_without_network(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"TRAFFIC_TOKEN": ""}), patch.object(stats, "fetch_snapshot") as fetch, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            stats.main(["--repository", "HKUDS/AutoSwarm", "--output", temporary])
        fetch.assert_not_called()
        with patch.object(stats, "request_json") as request, self.assertRaises(ValueError):
            stats.fetch_snapshot("../another-host", "test-only-token")
        request.assert_not_called()

    def test_permission_error_does_not_expose_token_or_response_body(self):
        error = urllib.error.HTTPError(stats.API, 403, "Forbidden", {}, io.BytesIO(b"sensitive-response"))
        with patch.object(stats.urllib.request, "urlopen", side_effect=error), self.assertRaises(RuntimeError) as caught:
            stats.request_json("/repos/HKUDS/AutoSwarm/traffic/clones", "test-only-token")
        self.assertIn("Administration: read", str(caught.exception))
        self.assertNotIn("test-only-token", str(caught.exception))
        self.assertNotIn("sensitive-response", str(caught.exception))

    def test_invalid_token_headers_fail_before_network_without_exposing_values(self):
        for token in ["two tokens", "secret\nheader", "bad-token-\u2603"]:
            with self.subTest(token_type="invalid header"), patch.object(stats.urllib.request, "urlopen") as request, self.assertRaises(ValueError) as caught:
                stats.request_json("/repos/HKUDS/AutoSwarm/traffic/clones", token)
            request.assert_not_called()
            self.assertNotIn(token, str(caught.exception))

    def test_missing_actions_secret_has_an_actionable_run_annotation(self):
        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            with patch.dict(os.environ, {"TRAFFIC_TOKEN": "", "GITHUB_ACTIONS": "true"}), \
                    patch.object(stats, "fetch_snapshot") as fetch, \
                    contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                stats.main(["--repository", "HKUDS/AutoSwarm", "--output", temporary])
            self.assertEqual(caught.exception.code, 1)
            self.assertTrue(stderr.getvalue().startswith("::error::Download statistics:"))
            self.assertIn("AUTOSWARM_TRAFFIC_TOKEN or TRAFFIC_TOKEN", stderr.getvalue())
            self.assertEqual(list(Path(temporary).iterdir()), [])
            fetch.assert_not_called()

    def test_actions_annotations_escape_control_characters_and_keep_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            stats.write_outputs(stats.merge_history(None, snapshot()), directory)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            stderr = io.StringIO()
            with patch.dict(os.environ, {"TRAFFIC_TOKEN": "test-only-token", "GITHUB_ACTIONS": "true"}), \
                    patch.object(stats, "fetch_snapshot", side_effect=RuntimeError("API 100% unavailable\r\nretry later")), \
                    contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                stats.main(["--repository", "HKUDS/AutoSwarm", "--output", temporary])
            self.assertIn("100%25 unavailable%0D%0Aretry later", stderr.getvalue())
            self.assertEqual(len(stderr.getvalue().splitlines()), 1)
            self.assertNotIn("test-only-token", stderr.getvalue())
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    def test_weekly_windows_keep_historical_days_without_double_counting(self):
        start = date(2026, 10, 10)
        rows = [((start + timedelta(days=n)).isoformat(), n + 1, 1) for n in range(14)]
        history = stats.merge_history(None, snapshot("2026-10-24", rows), start.isoformat())
        later = snapshot("2026-10-31", [((start + timedelta(days=n)).isoformat(), n + 1, 1) for n in range(7, 21)])
        history = stats.merge_history(history, later)
        self.assertEqual(stats.totals(history)["clones"], 231)
        self.assertEqual(len(history["clones"]), 21)
        self.assertEqual(stats.totals(stats.merge_history(history, later))["clones"], 231)

    def test_cli_preserves_backfilled_start_without_a_since_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            history = stats.merge_history(None, snapshot("2026-10-24", [("2026-10-10", 100, 2), ("2026-10-23", 10, 1)]), "2026-10-10")
            stats.write_outputs(history, directory)
            later = snapshot("2026-10-25", [("2026-10-23", 12, 1), ("2026-10-24", 3, 1)])
            for _ in range(2):
                with patch.dict(os.environ, {"TRAFFIC_TOKEN": "test-only-token"}), \
                        patch.object(stats, "fetch_snapshot", return_value=later), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(stats.main(["--repository", "HKUDS/AutoSwarm", "--output", str(directory)]), 0)
                saved = json.loads((directory / "history.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["since"], "2026-10-10")
                self.assertEqual(saved["started_at"], history["started_at"])
                self.assertEqual(stats.totals(saved)["clones"], 115)


if __name__ == "__main__":
    unittest.main()

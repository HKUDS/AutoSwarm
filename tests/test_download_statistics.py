"""Long-lived counters must survive overlapping windows, retries and removals."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stats = load("collect_downloads")
archive = load("archive_download_statistics")


class Response:
    def __init__(self, body: bytes, headers=None, status=200, url="https://example.invalid/"):
        self.body, self.headers, self.status, self.url = body, headers or {}, status, url
    def read(self): return self.body
    def geturl(self): return self.url
    def __enter__(self): return self
    def __exit__(self, *exc): return False


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

    def test_desk_updates_and_update_files_are_counted_apart_from_downloads(self):
        names = {1: "AutoSwarm-Desk-Setup.exe", 2: "AutoSwarm-Desk-0.9.8-win-x64.zip", 3: "AutoSwarm-Desk-Update.exe",
                 4: "AutoSwarm-Desk-Update.exe.blockmap", 5: "latest.yml", 6: "通用 wheel.whl"}
        assets = [{**asset(n, n * 10), "name": name} for n, name in names.items()]
        history = stats.merge_history(None, snapshot(assets=assets))
        values = stats.totals(history)
        self.assertEqual((values["release_downloads"], values["desk_downloads"], values["desk_updates"]), (90, 30, 30))
        rendered = stats.outputs(history)
        self.assertEqual(json.loads(rendered["release-downloads-badge.json"])["message"], "90")
        self.assertIn("| of which AutoSwarm Desk (installer and ZIP) | 30 |", rendered["README.md"])
        self.assertIn("| AutoSwarm Desk automatic updates (not included above) | 30 |", rendered["README.md"])

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
        def api(path, token, raw=None, **label):
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


def traffic_response(day="2026-11-21", clones=4, uniques=2, expiration=None):
    body = json.dumps({"count": clones, "uniques": uniques,
                       "clones": [{"timestamp": day + "T00:00:00Z", "count": clones, "uniques": uniques}]}, indent=2).encode()
    return {"file": "traffic-clones.json", "url": stats.API + "/repos/HKUDS/AutoSwarm/traffic/clones?per=day", "status": 200,
            "date": "Sun, 22 Nov 2026 12:00:00 GMT", "request_id": "ABCD:1234", "token_expiration": expiration, "body": body}


class EvidenceTests(unittest.TestCase):
    def test_fetch_keeps_response_bytes_and_receipts_but_not_the_release_list(self):
        traffic = traffic_response()["body"]
        bodies = {"/traffic/clones?per=day": traffic,
                  "/releases?per_page=100&page=1": json.dumps([{"id": 7, "draft": False, "tag_name": "v1"},
                                                                {"id": 8, "draft": True, "tag_name": "unpublished-draft"}]).encode(),
                  "/releases/7/assets?per_page=100&page=1": json.dumps([asset(70, 5)], ensure_ascii=False).encode()}
        def urlopen(request):
            return Response(bodies[request.full_url.split("/repos/HKUDS/AutoSwarm", 1)[1]],
                            {"Date": "Sun, 22 Nov 2026 12:00:00 GMT", "X-GitHub-Request-Id": "ABCD:1234",
                             "GitHub-Authentication-Token-Expiration": "2027-01-01 00:00:00 UTC"})
        with patch.object(stats.urllib.request, "urlopen", side_effect=urlopen):
            result = stats.fetch_snapshot("HKUDS/AutoSwarm", "test-only-token")
        files = {response["file"]: response for response in result["responses"]}
        self.assertEqual(set(files), {"traffic-clones.json", "release-7-assets-1.json"})
        self.assertEqual(files["traffic-clones.json"]["body"], traffic)
        self.assertEqual((files["release-7-assets-1.json"]["release_id"], files["release-7-assets-1.json"]["tag"]), (7, "v1"))
        self.assertEqual(files["release-7-assets-1.json"]["request_id"], "ABCD:1234")
        self.assertNotIn(b"unpublished-draft", b"".join(response["body"] for response in result["responses"]))
        self.assertEqual(result["releases"][0]["assets"][0]["download_count"], 5)

    def test_snapshot_files_are_byte_exact_with_digests_and_never_overwritten(self):
        response = traffic_response(expiration="2027-01-01 00:00:00 UTC")
        taken = {**snapshot("2026-11-22"), "responses": [response]}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            name = stats.write_snapshot(taken, {"clones": 4}, directory, {"environment": "local"})
            self.assertEqual(name, "snapshots/2026-11-22T120000Z")
            self.assertEqual((directory / name / "traffic-clones.json").read_bytes(), response["body"])
            raw_manifest = (directory / name / "manifest.json").read_bytes()
            self.assertNotIn(b"\r\n", raw_manifest)
            entry = json.loads(raw_manifest)["responses"][0]
            self.assertEqual((entry["sha256"], entry["bytes"]), (hashlib.sha256(response["body"]).hexdigest(), len(response["body"])))
            self.assertEqual((entry["date"], entry["request_id"]), ("Sun, 22 Nov 2026 12:00:00 GMT", "ABCD:1234"))
            self.assertNotIn("token_expiration", entry)
            with self.assertRaises(FileExistsError):
                stats.write_snapshot(taken, {}, directory, {})
            for file in ["../history.json", "manifest.json", "Traffic.JSON"]:
                bad = {**taken, "observed_at": "2026-11-23T12:00:00+00:00", "responses": [{**response, "file": file}]}
                with self.subTest(file=file), self.assertRaises(ValueError):
                    stats.write_snapshot(bad, {}, directory, {})
            self.assertEqual(sorted(p.name for p in (directory / "snapshots").iterdir()), ["2026-11-22T120000Z"])
        self.assertEqual(stats.outputs(stats.merge_history(None, snapshot()))[".gitattributes"], "snapshots/** -text\n")

    def test_cli_writes_a_snapshot_logs_the_collection_and_reports_outputs(self):
        later = {**snapshot("2026-11-22", [("2026-11-21", 4, 2)], [asset(9, 3, "2026-11-20")]),
                 "responses": [traffic_response(expiration="2026-12-12 12:00:00 UTC")]}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "github-output"
            with patch.dict(os.environ, {"TRAFFIC_TOKEN": "test-only-token", "GITHUB_OUTPUT": str(output)}), \
                    patch.object(stats, "fetch_snapshot", return_value=later), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(stats.main(["--repository", "HKUDS/AutoSwarm", "--since", "2026-11-15", "--output", temporary]), 0)
            self.assertEqual(output.read_text(encoding="utf-8").splitlines(), ["snapshot=snapshots/2026-11-22T120000Z", "token-days-left=20"])
            saved = json.loads((directory / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["collections"], [{"observed_at": "2026-11-22T12:00:00+00:00", "latest_clone_day": "2026-11-21",
                                                     "release_downloads": 3, "desk_downloads": 0, "desk_updates": 0,
                                                     "snapshot": "snapshots/2026-11-22T120000Z"}])
            manifest = json.loads((directory / "snapshots/2026-11-22T120000Z/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["totals_after_merge"]["release_downloads"], 3)
            readme = (directory / "README.md").read_text(encoding="utf-8")
            self.assertIn("[signed snapshot](snapshots/2026-11-22T120000Z/)", readme)
            self.assertIn("gh attestation verify snapshots/2026-11-22T120000Z/traffic-clones.json", readme)
            self.assertIn("--signer-workflow HKUDS/AutoSwarm/.github/workflows/download-statistics.yml", readme)

    def test_collection_log_is_rebuilt_once_from_earlier_statistics_commits(self):
        first = stats.merge_history(None, snapshot("2026-10-24", assets=[asset(1, 2)]), "2026-10-24")
        other_start = stats.merge_history(None, snapshot("2026-10-23"), "2026-10-20")
        second = stats.merge_history(first, snapshot("2026-10-31", assets=[asset(1, 7)]))
        commits = [("a" * 40, "maintainer", other_start), ("b" * 40, "maintainer", first),
                   ("c" * 40, "github-actions[bot]", second), ("d" * 40, "github-actions[bot]", second)]
        rebuilt = stats.backfill_collections(second, commits)
        self.assertEqual([(row["commit"][0], row["release_downloads"]) for row in rebuilt["collections"]], [("b", 2), ("c", 7)])
        self.assertNotIn("collections", second)
        self.assertIs(stats.backfill_collections(rebuilt, commits), rebuilt)
        readme = stats.outputs(rebuilt)["README.md"]
        self.assertIn("| 2026-10-24 12:00 | pending | 2 |  | 0 | 0 | [commit bbbbbbb](https://github.com/HKUDS/AutoSwarm/commit/" + "b" * 40 + ") by maintainer, not signed |", readme)
        self.assertIn("| 2026-10-31 12:00 | pending | 7 | +5 | 0 | 0 | [commit ccccccc]", readme)

    def test_statistics_commits_reads_each_history_version_oldest_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            def git(*args):
                subprocess.run(["git", "-c", "user.name=Stats Bot", "-c", "user.email=bot@example.invalid", "-c", "commit.gpgsign=false", *args],
                               cwd=temporary, check=True, capture_output=True)
            git("init", "-q", "-b", "stats")
            for n in (1, 2):
                Path(temporary, "history.json").write_text(json.dumps({"n": n}), encoding="utf-8")
                git("add", "history.json")
                git("commit", "-q", "-m", f"collection {n}")
            Path(temporary, "README.md").write_text("documentation only", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-q", "-m", "docs")
            commits = list(stats.statistics_commits("stats", Path(temporary)))
        self.assertEqual([(author, history["n"]) for _, author, history in commits], [("Stats Bot", 1), ("Stats Bot", 2)])
        self.assertTrue(all(len(sha) == 40 for sha, _, _ in commits))

    def test_weekly_clones_use_sunday_to_saturday_weeks_from_the_start_date(self):
        rows = [("2026-10-09", 50, 1), ("2026-10-10", 1, 1), ("2026-10-11", 2, 1), ("2026-10-17", 3, 1), ("2026-10-18", 4, 1)]
        history = stats.merge_history(None, snapshot("2026-10-19", rows), "2026-10-10")
        self.assertEqual(stats.weekly_clones(history), [(date(2026, 10, 18), date(2026, 10, 24), 4, 1),
                                                        (date(2026, 10, 11), date(2026, 10, 17), 5, 2),
                                                        (date(2026, 10, 4), date(2026, 10, 10), 1, 1)])
        self.assertIn("| 2026-10-11 – 2026-10-17 | 5 | 2/7 |", stats.outputs(history)["README.md"])

    def test_token_expiry_accepts_githubs_header_formats_only(self):
        now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        self.assertEqual(stats.token_days_left("2026-12-22 12:00:00 UTC", now), 30)
        self.assertEqual(stats.token_days_left("2026-12-22 04:00:00 -0800", now), 30)
        self.assertEqual(stats.token_days_left("2026-12-22 11:59:59 UTC", now), 29)
        for value in (None, "", "never", "2026-12-22"):
            self.assertIsNone(stats.token_days_left(value, now))

    def test_run_details_name_the_actions_run(self):
        self.assertEqual(stats.run_details({}), {"environment": "local"})
        details = stats.run_details({"GITHUB_ACTIONS": "true", "GITHUB_SERVER_URL": "https://github.com",
                                     "GITHUB_REPOSITORY": "HKUDS/AutoSwarm", "GITHUB_RUN_ID": "42", "RUNNER_ENVIRONMENT": "github-hosted"})
        self.assertEqual((details["run_url"], details["runner_environment"]), ("https://github.com/HKUDS/AutoSwarm/actions/runs/42", "github-hosted"))


class Opener:
    def __init__(self, *results):
        self.results, self.requests = list(results), []
    def open(self, request, timeout=None):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://example.invalid/", code, "status", headers or {}, None)


class ArchiveTests(unittest.TestCase):
    def test_evidence_urls_pin_the_snapshot_to_its_commit(self):
        urls = archive.evidence_urls("HKUDS/AutoSwarm", "f" * 40, "snapshots/2026-11-22T120000Z", ["manifest.json", "attestation.sigstore.json"])
        self.assertEqual(urls, ["https://api.github.com/repos/HKUDS/AutoSwarm/releases?per_page=100",
                                f"https://raw.githubusercontent.com/HKUDS/AutoSwarm/{'f' * 40}/snapshots/2026-11-22T120000Z/attestation.sigstore.json",
                                f"https://raw.githubusercontent.com/HKUDS/AutoSwarm/{'f' * 40}/snapshots/2026-11-22T120000Z/manifest.json"])

    def test_wayback_takes_the_redirect_to_a_new_capture_without_following_it(self):
        capture = "https://web.archive.org/web/20261122120000/https://example.org/a"
        opener = Opener(http_error(302, {"Location": capture}))
        with patch.object(archive.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(archive.wayback("https://example.org/a", sleep=lambda _: None), (capture, "saved"))
        build.assert_called_once_with(archive._KeepRedirect)
        self.assertEqual(opener.requests[0].full_url, "https://web.archive.org/save/https://example.org/a")

    def test_wayback_retries_throttling_and_reports_failures(self):
        waits = []
        opener = Opener(http_error(429), Response(b"", {"Content-Location": "/web/2026/https://example.org/a"}))
        with patch.object(archive.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(archive.wayback("https://example.org/a", sleep=waits.append), ("https://web.archive.org/web/2026/https://example.org/a", "saved"))
        self.assertEqual(waits, [30])
        with patch.object(archive.urllib.request, "build_opener", return_value=Opener(http_error(404))):
            self.assertEqual(archive.wayback("https://example.org/a", sleep=waits.append), (None, "HTTP 404"))
        waits.clear()
        timeouts = Opener(*[urllib.error.URLError("timed out")] * 3)
        with patch.object(archive.urllib.request, "build_opener", return_value=timeouts):
            self.assertEqual(archive.wayback("https://example.org/a", sleep=waits.append), (None, "timed out"))
        self.assertEqual(waits, [30, 60])
        with patch.object(archive.urllib.request, "build_opener", return_value=Opener(Response(b"<html>error</html>"))):
            self.assertEqual(archive.wayback("https://example.org/a", sleep=waits.append), (None, "HTTP 200 without a capture address"))

    def test_software_heritage_save_request(self):
        accepted = Opener(Response(json.dumps({"save_request_status": "accepted", "save_task_status": "pending"}).encode()))
        with patch.object(archive.urllib.request, "build_opener", return_value=accepted):
            self.assertEqual(archive.software_heritage("HKUDS/AutoSwarm", sleep=lambda _: None), (True, "request accepted, task pending"))
        request = accepted.requests[0]
        self.assertEqual((request.get_method(), request.data), ("POST", b""))
        self.assertEqual(request.full_url, "https://archive.softwareheritage.org/api/1/origin/save/git/url/https://github.com/HKUDS/AutoSwarm/")
        rejected = Opener(Response(json.dumps({"save_request_status": "rejected"}).encode()))
        with patch.object(archive.urllib.request, "build_opener", return_value=rejected):
            self.assertFalse(archive.software_heritage("HKUDS/AutoSwarm", sleep=lambda _: None)[0])

    def test_main_reports_each_archive_and_warns_without_failing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "snapshots/2026-11-22T120000Z").mkdir(parents=True)
            (directory / "snapshots/2026-11-22T120000Z/manifest.json").write_text("{}", encoding="utf-8")
            summary, stdout = directory / "summary.md", io.StringIO()
            saved = lambda url, **_: (None, "HTTP 520") if "manifest" in url else ("https://web.archive.org/web/1/" + url, "saved")
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), patch.object(archive, "wayback", side_effect=saved), \
                    patch.object(archive, "software_heritage", return_value=(True, "request accepted, task pending")), contextlib.redirect_stdout(stdout):
                code = archive.main(["--repository", "HKUDS/AutoSwarm", "--commit", "a" * 40, "--snapshot", "snapshots/2026-11-22T120000Z",
                                     "--directory", temporary], sleep=lambda _: None)
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue().count("::warning::"), 1)
            self.assertIn("did not save https://raw.githubusercontent.com/HKUDS/AutoSwarm/" + "a" * 40, stdout.getvalue())
            report = summary.read_text(encoding="utf-8")
            self.assertIn("| Software Heritage | https://github.com/HKUDS/AutoSwarm | request accepted, task pending |", report)
            self.assertEqual(report.count("| Wayback Machine |"), 2)
            for bad in ({"--commit": "main"}, {"--snapshot": "../history.json"}):
                arguments = {"--repository": "HKUDS/AutoSwarm", "--commit": "a" * 40, "--snapshot": "snapshots/2026-11-22T120000Z",
                             "--directory": temporary, **bad}
                with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    archive.main([part for pair in arguments.items() for part in pair])


if __name__ == "__main__":
    unittest.main()

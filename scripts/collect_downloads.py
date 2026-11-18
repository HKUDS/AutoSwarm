"""Archive GitHub clone traffic and Release asset counters using only stdlib."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


API = "https://api.github.com"
FILES = ("history.json", "README.md", "clones-badge.json", "release-downloads-badge.json", ".gitattributes")
SNAPSHOTS = "snapshots"
MANIFEST = "manifest.json"
ATTESTATION = "attestation.sigstore.json"
WORKFLOW = ".github/workflows/download-statistics.yml"
# Snapshot digests cover the exact bytes; a checkout must not rewrite line endings.
GITATTRIBUTES = SNAPSHOTS + "/** -text\n"


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Statistics timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def count(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("GitHub counters must be nonnegative integers")
    return value


def request_json(path: str, token: str, raw: list | None = None, **label):
    """Return parsed JSON; with raw, also keep the response bytes and GitHub's receipt headers."""
    token = token.strip()
    if not token or not token.isascii() or any(character.isspace() for character in token):
        raise ValueError("TRAFFIC_TOKEN must contain a single GitHub access token")
    request = urllib.request.Request(API + path, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "AutoSwarm-download-statistics",
    })
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read()
            status, headers = response.status, response.headers
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"GitHub {path}: HTTP {error.code}. Traffic requires TRAFFIC_TOKEN with "
            "Administration: read; Release assets require Contents: read. Previous history is unchanged."
        ) from None
    if raw is not None:
        raw.append({**label, "url": API + path, "status": status, "date": headers.get("Date"),
                    "request_id": headers.get("X-GitHub-Request-Id"),
                    "token_expiration": headers.get("GitHub-Authentication-Token-Expiration"), "body": body})
    return json.loads(body)


def pages(path: str, token: str, raw: list | None = None, file: str = "", **label):
    page = 1
    while True:
        batch = request_json(f"{path}?per_page=100&page={page}", token, raw, file=file.format(page=page), **label)
        if not isinstance(batch, list):
            raise ValueError("GitHub returned an invalid paginated list")
        yield from batch
        if len(batch) < 100:
            return
        page += 1


def fetch_snapshot(repository: str, token: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository) or repository.split("/")[-1] in {".", ".."}:
        raise ValueError("Use a GitHub repository in owner/name form")
    prefix = "/repos/" + repository
    responses = []
    clones = request_json(prefix + "/traffic/clones?per=day", token, responses, file="traffic-clones.json")
    releases = []
    # The release list itself is not kept: with this token it also lists unpublished drafts.
    for release in pages(prefix + "/releases", token):
        if release["draft"]:
            continue
        identity = count(release["id"])
        assets = list(pages(prefix + f"/releases/{identity}/assets", token, responses,
                            f"release-{identity}-assets-{{page}}.json", release_id=identity, tag=release["tag_name"]))
        releases.append({"id": identity, "tag": release["tag_name"], "assets": assets})
    return {"repository": repository, "observed_at": datetime.now(timezone.utc).isoformat(),
            "clones": clones, "releases": releases, "responses": responses}


def merge_history(history: dict | None, snapshot: dict, since: str | None = None) -> dict:
    observed = timestamp(snapshot["observed_at"])
    repository = snapshot["repository"]
    if history is None:
        start = date.fromisoformat(since) if since else observed.date()
        history = {"schema_version": 1, "repository": repository, "since": start.isoformat(),
                   "started_at": observed.isoformat(), "last_collected_at": observed.isoformat(),
                   "clones": {}, "release_assets": {}}
    else:
        history = copy.deepcopy(history)
        if history["schema_version"] != 1 or history["repository"] != repository:
            raise ValueError("History schema or repository does not match; keep the original history")
        if since and date.fromisoformat(since).isoformat() != history["since"]:
            raise ValueError("The statistics start date cannot be changed on existing history")
        if observed < timestamp(history["last_collected_at"]):
            raise ValueError("Refusing to replace history with an older snapshot")
    start = date.fromisoformat(history["since"])
    if start > observed.date():
        raise ValueError("The statistics start date cannot be in the future")
    for row in snapshot["clones"]["clones"]:
        day = timestamp(row["timestamp"]).date().isoformat()
        history["clones"][day] = {"count": count(row["count"]), "uniques": count(row["uniques"])}
    # Absent assets keep their last observed count, including deleted releases.
    for asset in history["release_assets"].values():
        asset["present"] = False
    for release in snapshot["releases"]:
        for asset in release["assets"]:
            key = str(count(asset["id"]))
            downloads = count(asset["download_count"])
            created = timestamp(asset["created_at"])
            previous = history["release_assets"].get(key)
            baseline = previous["baseline"] if previous else (downloads if created.date() < start else 0)
            history["release_assets"][key] = {
                "name": asset["name"], "tag": release["tag"], "created_at": created.isoformat(),
                "baseline": baseline, "count": downloads, "present": True,
                "last_seen_at": observed.isoformat(),
            }
    history["last_collected_at"] = observed.isoformat()
    return history


def asset_kind(name: str) -> str:
    """AutoSwarm Desk uploads its installer twice: AutoSwarm-Desk-Setup.exe for people, and
    AutoSwarm-Desk-Update.exe with latest.yml and a .blockmap read only by Desk's automatic update."""
    if name.endswith((".yml", ".yaml", ".blockmap")):
        return "update_metadata"
    if name.startswith("AutoSwarm-Desk-Update"):
        return "desk_update"
    return "desk_download" if name.startswith("AutoSwarm-Desk") else "download"


def downloads(history: dict, *kinds: str) -> int:
    return sum(max(0, count(a["count"]) - count(a["baseline"])) for a in history["release_assets"].values() if asset_kind(a["name"]) in kinds)


def totals(history: dict) -> dict:
    days = {day: row for day, row in history["clones"].items() if day >= history["since"]}
    missing = []
    if days:
        day, end = date.fromisoformat(history["since"]), date.fromisoformat(max(days))
        while day <= end:
            if day.isoformat() not in days:
                missing.append(day.isoformat())
            day += timedelta(days=1)
    return {
        "clones": sum(count(row["count"]) for row in days.values()) if days else None,
        "latest_clone_day": max(history["clones"], default=None),
        "missing_clone_dates": missing,
        # Downloads by people; automatic updates and the files they read are counted apart.
        "release_downloads": downloads(history, "download", "desk_download"),
        "desk_downloads": downloads(history, "desk_download"),
        "desk_updates": downloads(history, "desk_update"),
    }


def collection(history: dict, **evidence) -> dict:
    """One row of the collection log: cumulative counters as of this collection and its evidence."""
    values = totals(history)
    return {"observed_at": history["last_collected_at"], "latest_clone_day": values["latest_clone_day"],
            **{key: values[key] for key in ("release_downloads", "desk_downloads", "desk_updates")}, **evidence}


def record_collection(history: dict, entry: dict) -> dict:
    history = copy.deepcopy(history)
    rows = [row for row in history.get("collections", []) if row["observed_at"] != entry["observed_at"]]
    history["collections"] = sorted(rows + [entry], key=lambda row: timestamp(row["observed_at"]))
    return history


def backfill_collections(history: dict, commits) -> dict:
    """Collections made before snapshots existed survive only as statistics-branch commits
    (oldest first: sha, author, history). Rebuild their log rows once; they carry no signature."""
    if "collections" in history:
        return history
    history = {**copy.deepcopy(history), "collections": []}
    for sha, author, earlier in commits:
        if earlier.get("repository") == history["repository"] and earlier.get("since") == history["since"]:
            if all(row["observed_at"] != earlier["last_collected_at"] for row in history["collections"]):
                history = record_collection(history, collection(earlier, commit=sha, author=author))
    return history


def statistics_commits(ref: str, cwd: Path | None = None):
    def git(*args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    for line in git("log", "--reverse", "--format=%H%x09%an", ref, "--", "history.json").splitlines():
        sha, author = line.split("\t", 1)
        yield sha, author, json.loads(git("show", f"{sha}:history.json"))


def snapshot_path(snapshot: dict) -> str:
    return SNAPSHOTS + "/" + timestamp(snapshot["observed_at"]).strftime("%Y-%m-%dT%H%M%SZ")


def run_details(environ=None) -> dict:
    """Where this collection ran. Informational: the attestation is the signed record of the same facts."""
    environ = os.environ if environ is None else environ
    if environ.get("GITHUB_ACTIONS") != "true":
        return {"environment": "local"}
    server, repository, run = (environ.get(key, "") for key in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return {"environment": "github-actions", "run_url": f"{server}/{repository}/actions/runs/{run}",
            "run_id": run, "run_attempt": environ.get("GITHUB_RUN_ATTEMPT"), "event": environ.get("GITHUB_EVENT_NAME"),
            "workflow_ref": environ.get("GITHUB_WORKFLOW_REF"), "commit": environ.get("GITHUB_SHA"),
            "runner_environment": environ.get("RUNNER_ENVIRONMENT")}


def write_snapshot(snapshot: dict, values: dict, directory: Path, run: dict) -> str:
    """Keep GitHub's responses byte for byte, with a manifest of their digests; never overwrite one."""
    files = [response["file"] for response in snapshot["responses"]]
    if any(not re.fullmatch(r"[a-z0-9-]+\.json", file) for file in files) or len(set(files)) != len(files) \
            or MANIFEST in files or not files:
        raise ValueError("Unexpected snapshot file names")
    name = snapshot_path(snapshot)
    target = directory / name
    target.mkdir(parents=True, exist_ok=False)
    entries = []
    for response in snapshot["responses"]:
        body = response["body"]
        (target / response["file"]).write_bytes(body)
        entries.append({**{key: value for key, value in response.items() if key not in ("body", "token_expiration")},
                        "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
    manifest = {
        "schema_version": 1, "repository": snapshot["repository"], "observed_at": snapshot["observed_at"],
        "collected_by": run, "responses": entries, "totals_after_merge": values,
        "note": "Each response file holds the exact bytes returned by the GitHub REST API. Published releases are "
                "kept as their asset lists; the release list itself is not kept because it can include unpublished drafts. "
                f"{ATTESTATION} is the Sigstore attestation issued to the workflow run for these files.",
    }
    (target / MANIFEST).write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return name


def token_days_left(value: str | None, now: datetime) -> int | None:
    """GitHub reports a token's expiry as e.g. '2027-01-31 12:00:00 UTC' or '... -0800'."""
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (UTC|[+-]\d{4})", (value or "").strip())
    if not match:
        return None
    zone = "+0000" if match[2] == "UTC" else match[2]
    return (datetime.strptime(f"{match[1]} {zone}", "%Y-%m-%d %H:%M:%S %z") - now).days


def weekly_clones(history: dict) -> list[tuple[date, date, int, int]]:
    """Sunday-Saturday UTC weeks, matching the Sunday collection, newest first: (start, end, clones, days)."""
    weeks: dict[date, tuple[int, int]] = {}
    for day, row in history["clones"].items():
        if day >= history["since"]:
            moment = date.fromisoformat(day)
            start = moment - timedelta(days=(moment.weekday() + 1) % 7)
            clones, recorded = weeks.get(start, (0, 0))
            weeks[start] = (clones + count(row["count"]), recorded + 1)
    return [(start, start + timedelta(days=6), clones, recorded) for start, (clones, recorded) in sorted(weeks.items(), reverse=True)]


def evidence_cell(row: dict, repository: str) -> str:
    if "snapshot" in row:
        return f"[signed snapshot]({row['snapshot']}/)"
    if "commit" in row:
        return f"[commit {row['commit'][:7]}](https://github.com/{repository}/commit/{row['commit']}) by {row['author']}, not signed"
    return "not recorded"


def outputs(history: dict) -> dict[str, str]:
    values = totals(history)
    repository = history["repository"]
    def badge(label, value, missing=False):
        return json.dumps({"schemaVersion": 1, "label": label,
                           "message": "pending" if value is None else f"{value:,}" + ("+" if missing else ""),
                           "color": "lightgrey" if value is None else "orange" if missing else "blue"}, indent=2) + "\n"
    clones = "Pending source data" if values["clones"] is None else f"{values['clones']:,}"
    missing = values["missing_clone_dates"]
    missing_note = "\nMissing clone dates: " + ", ".join(missing) + ". The total includes recorded dates only.\n" if missing else ""
    weeks = "".join(f"| {start} – {end} | {clones:,} | {recorded}/7 |\n" for start, end, clones, recorded in weekly_clones(history))
    log, previous = [], None
    for row in history.get("collections", []):
        change = "" if previous is None else f"{row['release_downloads'] - previous:+,}"
        log.append(f"| {timestamp(row['observed_at']).strftime('%Y-%m-%d %H:%M')} | {row['latest_clone_day'] or 'pending'} | "
                   f"{row['release_downloads']:,} | {change} | {row['desk_downloads']:,} | {row['desk_updates']:,} | {evidence_cell(row, repository)} |\n")
        previous = row["release_downloads"]
    example = next((row["snapshot"] for row in reversed(history.get("collections", [])) if "snapshot" in row), SNAPSHOTS + "/<collection>")
    readme = (
        "# AutoSwarm download statistics\n\n"
        f"Repository: [{repository}](https://github.com/{repository})  \n"
        f"Counting from: **{history['since']} (UTC)**  \n"
        f"Last collected: {history['last_collected_at']}  \n"
        f"Latest clone date returned by GitHub: {values['latest_clone_day'] or 'pending'}\n\n"
        "| Metric | Recorded count |\n| --- | ---: |\n"
        f"| Full repository clones since {history['since']} | {clones} |\n"
        f"| Release asset downloads since the baseline | {values['release_downloads']:,} |\n"
        f"| of which AutoSwarm Desk (installer and ZIP) | {values['desk_downloads']:,} |\n"
        f"| AutoSwarm Desk automatic updates (not included above) | {values['desk_updates']:,} |\n"
        + missing_note + "\n"
        "Daily clone records are merged by UTC date, including later corrections. Dates not returned by GitHub are not filled with zero; data after the latest reported date is pending. Daily unique cloners remain separate and are never summed into lifetime users.\n\n"
        "Release counters are retained by asset ID, even after removal. Assets created before the start date begin at their first observed counter; earlier downloads cannot be dated. New assets created on/after the start date include their full counter. Re-uploading creates a separate asset. AutoSwarm Desk's automatic update downloads its own copy of the installer (AutoSwarm-Desk-Update.exe); those downloads and the update files it reads (latest.yml, .blockmap) are not counted as release downloads.\n\n"
        "These are separate acquisition metrics, not a count of installations or users. Git fetch/pull, raw files, automatic source archives and PyPI downloads are outside these counters.\n\n"
        "## Weekly clones\n\n"
        "Sunday to Saturday (UTC), so each Sunday collection completes the week before it. Weeks with fewer than 7 recorded days are partial: the start date, days GitHub has not reported yet, or missing days. Unique cloners are daily figures and are not added up per week.\n\n"
        "| Week (UTC) | Clones | Days recorded |\n| --- | ---: | ---: |\n" + (weeks or "| pending | | |\n") + "\n"
        "## Collections\n\n"
        "Release counters are cumulative; the change is the number of downloads between two collections.\n\n"
        "| Collected (UTC) | Clone data through | Release downloads | Change | of which Desk | Desk automatic updates | Evidence |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | --- |\n" + "".join(reversed(log)) + "\n"
        "## Evidence\n\n"
        f"Each signed snapshot in `{SNAPSHOTS}/` keeps the GitHub API responses of that collection byte for byte, `{MANIFEST}` with their SHA-256 digests and GitHub's response date and request ID, and `{ATTESTATION}`, a Sigstore attestation issued to the workflow run that fetched them. "
        "The attestation names the repository, workflow file, commit and run, and is recorded in Sigstore's public transparency log. To verify a file with a current GitHub CLI:\n\n"
        f"```\ngh attestation verify {example}/traffic-clones.json \\\n  --bundle {example}/{ATTESTATION} \\\n"
        f"  --repo {repository} --signer-workflow {repository}/{WORKFLOW} \\\n  --source-ref refs/heads/main --deny-self-hosted-runners\n```\n\n"
        "Collections listed with a commit predate signed snapshots; their figures are only recorded by that commit.\n\n"
        "[Machine-readable history](history.json) · [Collector documentation](https://github.com/" + repository + "/blob/main/docs/download-statistics.md)\n"
    )
    return {"history.json": json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            "README.md": readme,
            "clones-badge.json": badge("clones since " + history["since"], values["clones"], bool(missing)),
            "release-downloads-badge.json": badge("release downloads since " + history["since"], values["release_downloads"]),
            ".gitattributes": GITATTRIBUTES}


def write_outputs(history: dict, directory: Path) -> None:
    rendered = outputs(history)
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".stats-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, directory / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def set_outputs(values: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in values.items() if value is not None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--since", help="First UTC day to count; retained once history exists")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backfill-ref", help="Git ref of the statistics branch; rebuilds the collection log from its commits when history has none")
    args = parser.parse_args(argv)
    try:
        token = os.environ.get("TRAFFIC_TOKEN")
        if not token:
            raise ValueError("Set TRAFFIC_TOKEN to a dedicated token with Administration: read and Contents: read. "
                             "In GitHub Actions, configure repository secret AUTOSWARM_TRAFFIC_TOKEN or TRAFFIC_TOKEN")
        path = args.output / "history.json"
        history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if history is not None and args.backfill_ref:
            history = backfill_collections(history, statistics_commits(args.backfill_ref))
        snapshot = fetch_snapshot(args.repository, token)
        merged = merge_history(history, snapshot, args.since)
        name = snapshot_path(snapshot) if snapshot.get("responses") else None
        merged = record_collection(merged, collection(merged, **({"snapshot": name} if name else {})))
        if name:
            write_snapshot(snapshot, totals(merged), args.output, run_details())
        write_outputs(merged, args.output)
        expiry = next((r["token_expiration"] for r in snapshot.get("responses", []) if r.get("token_expiration")), None)
        days_left = token_days_left(expiry, timestamp(snapshot["observed_at"]))
        set_outputs({"snapshot": name, "token-days-left": days_left})
        print(json.dumps({"repository": args.repository, "since": merged["since"], "snapshot": name,
                          "token_days_left": days_left, **totals(merged)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.CalledProcessError) as error:
        message = f"Download statistics: {error}"
        if os.environ.get("GITHUB_ACTIONS") == "true":
            message = "::error::" + message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

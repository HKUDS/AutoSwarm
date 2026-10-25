"""Archive GitHub clone traffic and Release asset counters using only stdlib."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


API = "https://api.github.com"
FILES = ("history.json", "README.md", "clones-badge.json", "release-downloads-badge.json")


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Statistics timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def count(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("GitHub counters must be nonnegative integers")
    return value


def request_json(path: str, token: str):
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
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"GitHub {path}: HTTP {error.code}. Traffic requires TRAFFIC_TOKEN with "
            "Administration: read; Release assets require Contents: read. Previous history is unchanged."
        ) from None


def pages(path: str, token: str):
    page = 1
    while True:
        batch = request_json(f"{path}?per_page=100&page={page}", token)
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
    clones = request_json(prefix + "/traffic/clones?per=day", token)
    releases = []
    for release in pages(prefix + "/releases", token):
        if release["draft"]:
            continue
        assets = list(pages(prefix + f"/releases/{release['id']}/assets", token))
        releases.append({"id": release["id"], "tag": release["tag_name"], "assets": assets})
    return {"repository": repository, "observed_at": datetime.now(timezone.utc).isoformat(),
            "clones": clones, "releases": releases}


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
        "release_downloads": sum(max(0, count(a["count"]) - count(a["baseline"])) for a in history["release_assets"].values()),
    }


def outputs(history: dict) -> dict[str, str]:
    values = totals(history)
    def badge(label, value, missing=False):
        return json.dumps({"schemaVersion": 1, "label": label,
                           "message": "pending" if value is None else f"{value:,}" + ("+" if missing else ""),
                           "color": "lightgrey" if value is None else "orange" if missing else "blue"}, indent=2) + "\n"
    clones = "Pending source data" if values["clones"] is None else f"{values['clones']:,}"
    missing = values["missing_clone_dates"]
    missing_note = "\nMissing clone dates: " + ", ".join(missing) + ". The total includes recorded dates only.\n" if missing else ""
    readme = (
        "# AutoSwarm download statistics\n\n"
        f"Repository: [{history['repository']}](https://github.com/{history['repository']})  \n"
        f"Counting from: **{history['since']} (UTC)**  \n"
        f"Last collected: {history['last_collected_at']}  \n"
        f"Latest clone date returned by GitHub: {values['latest_clone_day'] or 'pending'}\n\n"
        "| Metric | Recorded count |\n| --- | ---: |\n"
        f"| Full repository clones since {history['since']} | {clones} |\n"
        f"| Release asset downloads since the baseline | {values['release_downloads']:,} |\n"
        + missing_note + "\n"
        "Daily clone records are merged by UTC date, including later corrections. Dates not returned by GitHub are not filled with zero; data after the latest reported date is pending. Daily unique cloners remain separate and are never summed into lifetime users.\n\n"
        "Release counters are retained by asset ID, even after removal. Assets created before the start date begin at their first observed counter; earlier downloads cannot be dated. New assets created on/after the start date include their full counter. Re-uploading creates a separate asset.\n\n"
        "These are separate acquisition metrics, not a count of installations or users. Git fetch/pull, raw files, automatic source archives and PyPI downloads are outside these counters.\n\n"
        "[Machine-readable history](history.json) · [Collector documentation](https://github.com/" + history["repository"] + "/blob/main/docs/download-statistics.md)\n"
    )
    return {"history.json": json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            "README.md": readme,
            "clones-badge.json": badge("clones since " + history["since"], values["clones"], bool(missing)),
            "release-downloads-badge.json": badge("release downloads since " + history["since"], values["release_downloads"])}


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--since", help="First UTC day to count; retained once history exists")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        token = os.environ.get("TRAFFIC_TOKEN")
        if not token:
            raise ValueError("Set TRAFFIC_TOKEN to a dedicated token with Administration: read and Contents: read")
        path = args.output / "history.json"
        history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        snapshot = fetch_snapshot(args.repository, token)
        merged = merge_history(history, snapshot, args.since)
        write_outputs(merged, args.output)
        print(json.dumps({"repository": args.repository, "since": merged["since"], **totals(merged)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f"Download statistics: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())

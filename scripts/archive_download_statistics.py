"""Ask independent public archives to keep copies of a published statistics snapshot.

Best effort: failures become run warnings and never undo the published statistics."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


WAYBACK = "https://web.archive.org"
SOFTWARE_HERITAGE = "https://archive.softwareheritage.org/api/1/origin/save/git/url/{origin}/"
AGENT = "AutoSwarm-download-statistics (+https://github.com/HKUDS/AutoSwarm/blob/main/docs/download-statistics.md)"
RETRYABLE = {408, 425, 429, 500, 502, 503, 504, 520, 522, 523, 524}


def evidence_urls(repository: str, commit: str, snapshot: str, files) -> list[str]:
    """Public release counters, then the snapshot files at their immutable commit address."""
    raw = f"https://raw.githubusercontent.com/{repository}/{commit}/{snapshot}/"
    return [f"https://api.github.com/repos/{repository}/releases?per_page=100", *(raw + name for name in sorted(files))]


class _KeepRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Report the redirect itself instead of following it.


def send(url: str, method: str = "GET", tries: int = 3, wait: float = 30, timeout: float = 180, sleep=time.sleep, follow: bool = True):
    """Return (status or None, headers, body or error); retry throttling, server errors and timeouts."""
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_KeepRedirect)
    for attempt in range(1, tries + 1):
        request = urllib.request.Request(url, data=b"" if method == "POST" else None, method=method, headers={"User-Agent": AGENT})
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in RETRYABLE or attempt == tries:
                return error.code, error.headers, f"HTTP {error.code}"
        except OSError as error:
            if attempt == tries:
                return None, {}, str(getattr(error, "reason", error)) or type(error).__name__
        sleep(wait * attempt)


def wayback(url: str, **options) -> tuple[str | None, str]:
    """Save Page Now; return (capture URL, detail). A new capture answers with a redirect to its
    address, which can take minutes to become viewable, so the redirect is not followed."""
    status, headers, detail = send(f"{WAYBACK}/save/{url}", follow=False, **options)
    location = (headers.get("Location") or headers.get("Content-Location") or "") if headers else ""
    if location.startswith("/web/"):
        location = WAYBACK + location
    if status in {200, 301, 302, 303, 307} and location.startswith(WAYBACK + "/web/"):
        return location, "saved"
    return None, detail if isinstance(detail, str) else f"HTTP {status} without a capture address"


def software_heritage(repository: str, **options) -> tuple[bool, str]:
    """Request a visit of the whole Git repository, statistics branch included."""
    status, _, body = send(SOFTWARE_HERITAGE.format(origin=f"https://github.com/{repository}"), method="POST", **options)
    if status != 200:
        return False, body if isinstance(body, str) else f"HTTP {status}"
    try:
        request = json.loads(body)
    except ValueError:
        return False, "unreadable response"
    state = request.get("save_request_status")
    return state in {"accepted", "pending"}, f"request {state}, task {request.get('save_task_status')}"


def main(argv=None, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True, help="Published statistics-branch commit")
    parser.add_argument("--snapshot", required=True, help="Snapshot directory, e.g. snapshots/2026-11-22T031705Z")
    parser.add_argument("--directory", type=Path, required=True, help="Checkout of the published statistics branch")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", args.repository) or not re.fullmatch(r"[0-9a-f]{40}", args.commit) \
            or not re.fullmatch(r"snapshots/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z", args.snapshot):
        parser.error("Use owner/name, a full commit SHA and a snapshots/<UTC time> directory")
    files = [path.name for path in (args.directory / args.snapshot).iterdir() if path.is_file()]
    if not files:
        parser.error("The snapshot directory is empty")
    rows, failures = [], []
    for index, url in enumerate(evidence_urls(args.repository, args.commit, args.snapshot, files)):
        if index:
            sleep(5)  # Save Page Now throttles bursts from one address.
        capture, detail = wayback(url, sleep=sleep)
        rows.append(("Wayback Machine", url, capture or detail))
        if not capture:
            failures.append(f"Wayback Machine did not save {url}: {detail}")
    accepted, detail = software_heritage(args.repository, sleep=sleep)
    rows.append(("Software Heritage", f"https://github.com/{args.repository}", detail))
    if not accepted:
        failures.append(f"Software Heritage did not accept the save request: {detail}")
    for failure in failures:
        print("::warning::" + failure.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
    report = "### Public archive copies\n\n| Archive | Address | Result |\n| --- | --- | --- |\n" + \
        "".join(f"| {archive} | {address} | {result} |\n" for archive, address, result in rows)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

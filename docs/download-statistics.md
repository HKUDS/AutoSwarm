# Download statistics

AutoSwarm records acquisition metrics for `HKUDS/AutoSwarm` from **2026-10-10 (UTC)**, including the 14 daily clone records returned by GitHub for October 10–23 when historical backfill was requested. Later reported dates are merged into this cumulative history. Collection runs in the repository, independently of the installed agent runtime. It requires no database or server.

| Metric | Source and interpretation |
| --- | --- |
| Repository clones | GitHub's daily full-clone records, merged by UTC date. Each collection refreshes the available 14-day window and retains older recorded days. Daily unique cloners are stored individually, never summed into lifetime users. |
| Release asset downloads | Uploaded asset counters, retained by asset ID. Assets created on/after the start date include their full count; older assets use their first observed counter as a baseline. Removed assets retain their last observed count; a re-upload with a new ID is separate. Counts downloads by people; AutoSwarm Desk's own update files are kept apart (below). |
| AutoSwarm Desk | Part of the Release asset downloads: `AutoSwarm-Desk-Setup.exe` and the ZIP (assets named `AutoSwarm-Desk…`), shown on their own row. Desk's automatic update downloads a separate copy of the installer, `AutoSwarm-Desk-Update.exe`, shown as automatic updates and not counted as downloads. An update usually counts once (a differential update fetches only changed parts, from the address the first request redirects to), and once more if it falls back to a full download. The update files it reads (`latest.yml`, `.blockmap`) are not counted. Keep these file names when publishing a Desk release (`npm run release` in `desktop/` prepares them). |

These counters do not measure installations or users and are not added together as a single total. They do not cover fetch/pull, raw files, GitHub's automatically generated source archives, or PyPI. GitHub source data can arrive late. Missing dates are not invented as zero; `pending` indicates no data since the start date, and `+`/orange indicates recorded counts with gaps. The latest source date and missing dates are shown on the statistics branch. Gaps outside GitHub's available traffic window cannot be reconstructed.

The statistics README also lists clones per week (Sunday to Saturday, UTC, so each Sunday collection completes the week before it) and a log of every collection with the Release counters at that time, the change since the previous collection, and its evidence.

## Evidence

Each collection keeps what GitHub returned, so the figures can be checked without trusting the maintainers of this repository:

- **Raw responses.** `snapshots/<UTC time>/` on `download-stats` holds the API responses byte for byte: `traffic-clones.json` (`/traffic/clones?per=day`) and `release-<id>-assets-<page>.json` for each published release. The release list itself is not kept, because with the traffic token it also lists unpublished drafts; leaving it out cannot raise any count.
- **Manifest.** `manifest.json` records each response's URL, SHA-256 digest and size, GitHub's `Date` and `X-GitHub-Request-Id` headers, the run that collected it, and the totals after merging.
- **Signature.** `attestation.sigstore.json` is a Sigstore bundle created by `actions/attest-build-provenance` for every JSON file of the snapshot. It binds their digests to the repository, workflow file, commit, ref, run and GitHub-hosted runner, and is entered in Sigstore's public, append-only transparency log, outside the control of this repository's administrators. GitHub also lists it under the repository's attestations, and the commit message links the run and the attestation.
- **Recovery copy.** The run uploads the snapshot as the artifact `download-statistics-snapshot` for 90 days, in case publishing fails. Verification does not need it.
- **Independent copies.** After publishing, the workflow asks the Internet Archive's Wayback Machine to save the public Releases API response (per-asset `download_count`, readable without a token) and each snapshot file at its commit-pinned `raw.githubusercontent.com` address, and asks Software Heritage to archive the repository, all branches included. These are timestamped copies held by third parties; a failed request is a run warning and does not affect the statistics. Look them up at `https://web.archive.org/web/*/raw.githubusercontent.com/HKUDS/AutoSwarm/*` and `https://archive.softwareheritage.org/browse/origin/?origin_url=https://github.com/HKUDS/AutoSwarm`.
- **Byte-exact checkout.** The branch's `.gitattributes` turns off line-ending conversion for `snapshots/`, so digests also match in Windows checkouts.

To verify a snapshot with the GitHub CLI:

```bash
git clone --branch download-stats https://github.com/HKUDS/AutoSwarm autoswarm-download-stats
cd autoswarm-download-stats
S=snapshots/2026-11-22T031705Z   # any signed snapshot
gh attestation verify "$S/traffic-clones.json" --bundle "$S/attestation.sigstore.json" \
  --repo HKUDS/AutoSwarm --signer-workflow HKUDS/AutoSwarm/.github/workflows/download-statistics.yml \
  --source-ref refs/heads/main --deny-self-hosted-runners
```

Repeat for the other JSON files of the snapshot; the one bundle covers them all. Without `--bundle`, the CLI fetches the attestation from GitHub instead. With `--format json`, the output includes the workflow commit (`sourceRepositoryDigest`) and the run (`runInvocationURI`); `scripts/collect_downloads.py` and the workflow at that commit show that the files are written directly from the API responses. The daily clone rows in `traffic-clones.json` and the `download_count` values in the asset lists can then be compared with the README.

What this shows: the files are exactly what this workflow, from the named commit on a GitHub-hosted runner, received from GitHub's API at that time. Editing them afterwards breaks verification, and producing different files would require changing the workflow, which the attestation identifies by commit. What it does not show: that the counts are people. GitHub's counters include CI, mirrors and automated clones, and the figures are only as reliable as GitHub's own reporting, as with a screenshot of the Traffic page.

Collections before the first signed snapshot (2026-10-25 to 2026-11-15) are listed with their commits only. They cannot be signed afterwards: a signature made later would only show that the data existed at that later time.

## Enable automatic collection

1. Create a dedicated fine-grained GitHub token for **HKUDS/AutoSwarm only**, with repository **Administration: read** and **Contents: read**. An organization may require approval. Store it as the repository Actions secret **`AUTOSWARM_TRAFFIC_TOKEN`**. The earlier name **`TRAFFIC_TOKEN`** is also supported; when both exist, `AUTOSWARM_TRAFFIC_TOKEN` takes precedence. The workflow passes the selected value to the collector's `TRAFFIC_TOKEN` environment variable. Do not put credentials in code, data files, or chat. The ordinary workflow `GITHUB_TOKEN` publishes the statistics branch; it is not the traffic-reading credential.
2. Publish `.github/workflows/download-statistics.yml` on the default branch. The job is restricted to the canonical `HKUDS/AutoSwarm` repository so development forks do not inflate the public counters.
3. Run **Actions → Download statistics → Run workflow** once. Confirm it creates/updates [`download-stats`](https://github.com/HKUDS/AutoSwarm/tree/download-stats).
4. Protect the statistics branch: **Settings → Rules → Rulesets → New branch ruleset**, target `download-stats`, enforcement **Active**, with **Restrict deletions** and **Block force pushes**. Leave pull request, status check, signed commit and update restrictions off; the workflow pushes ordinary fast-forward commits. Administrators can still change rulesets, so this prevents accidents; the signatures and independent copies are what make changes detectable.

The workflow grants itself `id-token: write` and `attestations: write` for signing; no further setup is needed in public repositories.

The workflow runs **every Sunday at 03:17 UTC (11:17 Hong Kong/Beijing time)**, and can also run manually. Each run refreshes the available 14-day window and merges by UTC date; overlapping records replace earlier observations instead of being added twice. Weekly collection leaves a margin for source delays and failed or delayed runs. Running exactly once every 14 days leaves no margin and can lose days that fall outside the API window. Overlapping manual/scheduled runs are serialized. The workflow loads existing history from `download-stats`, or uses `download-statistics-baseline.json` for the first run, preserving the saved counting start date. The first run after signed snapshots were introduced also rebuilds the collection log from the branch's earlier commits. It publishes only `history.json`, the statistics README, two badge JSON files, `.gitattributes` and the new snapshot on that branch; earlier snapshots are never rewritten. Main code and runtime configuration are not modified by collection. A failed API request or invalid state fails the job before publication; existing remote history remains intact. A rerun can recover days still returned by GitHub.

GitHub reports a fine-grained token's expiry with each response. When fewer than 30 days remain, the run publishes as usual and then fails with an annotation naming the date, so the failure notification arrives while there is time to replace `AUTOSWARM_TRAFFIC_TOKEN`. GitHub sends failure notifications of scheduled runs to the person who last changed the workflow's `cron` line. If the token lapses for more than 14 days, the missing clone days cannot be recovered.

GitHub schedules may be delayed and are automatically disabled after prolonged inactivity in public repositories. Check that the workflow remains enabled; weekly collection does not guarantee recovery after an outage longer than GitHub's source retention window. The workflow uses Node 24 versions of the checkout and Python setup actions. If the collector fails, its error appears directly in the run annotations, including accepted Secret names when the credential is missing.

## Manual collection

With `TRAFFIC_TOKEN` available securely in the process environment:

```bash
python scripts/collect_downloads.py --repository HKUDS/AutoSwarm --since 2026-10-10 --output /path/to/statistics
```

Keep the same output directory's `history.json` for subsequent runs. For the original baseline, copy `docs/download-statistics-baseline.json` there as `history.json` before the first collection. Repository and start date must match existing history. This command writes local files only, including an unsigned snapshot whose manifest records a local collection. Only the workflow signs and publishes snapshots.

## README badges

After the first successful publication, use Shields' endpoint badges backed by the public `clones-badge.json` and `release-downloads-badge.json` on `download-stats`, linking to its README. Keep the labels **clones since 2026-10-10** and **Release downloads since 2026-10-10** so the metrics are not confused with users or an all-channel download total. Badges are generated by the collector rather than incremented on page views.

Sources: [GitHub traffic API](https://docs.github.com/en/rest/metrics/traffic#get-repository-clones), [Release assets API](https://docs.github.com/en/rest/releases/assets#list-release-assets), [scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds), [`gh attestation verify`](https://cli.github.com/manual/gh_attestation_verify), [Wayback Machine](https://web.archive.org/), [Software Heritage](https://www.softwareheritage.org/).

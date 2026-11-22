# AutoSwarm download statistics

Repository: [HKUDS/AutoSwarm](https://github.com/HKUDS/AutoSwarm)  
Counting from: **2026-10-10 (UTC)**  
Last collected: 2026-11-22T11:26:39.341787+00:00  
Latest clone date returned by GitHub: 2026-11-21

| Metric | Recorded count |
| --- | ---: |
| Full repository clones since 2026-10-10 | 9,472 |
| Release asset downloads since the baseline | 690 |
| of which AutoSwarm Desk (installer and ZIP) | 690 |
| AutoSwarm Desk automatic updates (not included above) | 30 |

Daily clone records are merged by UTC date, including later corrections. Dates not returned by GitHub are not filled with zero; data after the latest reported date is pending. Daily unique cloners remain separate and are never summed into lifetime users.

Release counters are retained by asset ID, even after removal. Assets created before the start date begin at their first observed counter; earlier downloads cannot be dated. New assets created on/after the start date include their full counter. Re-uploading creates a separate asset. AutoSwarm Desk's automatic update downloads its own copy of the installer (AutoSwarm-Desk-Update.exe); those downloads and the update files it reads (latest.yml, .blockmap) are not counted as release downloads.

These are separate acquisition metrics, not a count of installations or users. Git fetch/pull, raw files, automatic source archives and PyPI downloads are outside these counters.

## Weekly clones

Sunday to Saturday (UTC), so each Sunday collection completes the week before it. Weeks with fewer than 7 recorded days are partial: the start date, days GitHub has not reported yet, or missing days. Unique cloners are daily figures and are not added up per week.

| Week (UTC) | Clones | Days recorded |
| --- | ---: | ---: |
| 2026-11-15 – 2026-11-21 | 1,099 | 7/7 |
| 2026-11-08 – 2026-11-14 | 2,746 | 7/7 |
| 2026-11-01 – 2026-11-07 | 2,034 | 7/7 |
| 2026-10-25 – 2026-10-31 | 2,228 | 7/7 |
| 2026-10-18 – 2026-10-24 | 589 | 7/7 |
| 2026-10-11 – 2026-10-17 | 738 | 7/7 |
| 2026-10-04 – 2026-10-10 | 38 | 1/7 |

## Collections

Release counters are cumulative; the change is the number of downloads between two collections.

| Collected (UTC) | Clone data through | Release downloads | Change | of which Desk | Desk automatic updates | Evidence |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 2026-11-22 11:26 | 2026-11-21 | 690 | +43 | 690 | 30 | [signed snapshot](snapshots/2026-11-22T112639Z/) |
| 2026-11-21 01:47 | 2026-11-19 | 647 | +129 | 647 | 25 | [signed snapshot](snapshots/2026-11-21T014707Z/) |
| 2026-11-18 09:07 | 2026-11-17 | 518 | +218 | 518 | 10 | [signed snapshot](snapshots/2026-11-18T090720Z/) |
| 2026-11-15 10:55 | 2026-11-14 | 300 | +244 | 300 | 7 | [commit 29c2da9](https://github.com/HKUDS/AutoSwarm/commit/29c2da99dd271ee489d9658427becccadea70fe8) by github-actions[bot], not signed |
| 2026-11-08 10:24 | 2026-11-07 | 56 | +56 | 56 | 6 | [commit fe6eb09](https://github.com/HKUDS/AutoSwarm/commit/fe6eb096eed33d12a642d3eb425ddfe33b35ed67) by github-actions[bot], not signed |
| 2026-11-01 10:29 | 2026-10-31 | 0 | +0 | 0 | 0 | [commit 4cf9da4](https://github.com/HKUDS/AutoSwarm/commit/4cf9da4086207298d6bd09aeea94e52a80ca0a0f) by github-actions[bot], not signed |
| 2026-10-25 08:26 | 2026-10-24 | 0 | +0 | 0 | 0 | [commit b1e590b](https://github.com/HKUDS/AutoSwarm/commit/b1e590bb3511fb589796db997040083b18fb85e9) by github-actions[bot], not signed |
| 2026-10-25 02:35 | 2026-10-23 | 0 | +0 | 0 | 0 | [commit b273962](https://github.com/HKUDS/AutoSwarm/commit/b273962f32763b8b61511d9166ee4d2aa1b17186) by github-actions[bot], not signed |
| 2026-10-25 02:34 | 2026-10-23 | 0 |  | 0 | 0 | [commit 3c94e1f](https://github.com/HKUDS/AutoSwarm/commit/3c94e1f9eb669957888541e9a1995e6e88edfb68) by LZH-YS1998, not signed |

## Evidence

Each signed snapshot in `snapshots/` keeps the GitHub API responses of that collection byte for byte, `manifest.json` with their SHA-256 digests and GitHub's response date and request ID, and `attestation.sigstore.json`, a Sigstore attestation issued to the workflow run that fetched them. The attestation names the repository, workflow file, commit and run, and is recorded in Sigstore's public transparency log. To verify a file with a current GitHub CLI:

```
gh attestation verify snapshots/2026-11-22T112639Z/traffic-clones.json \
  --bundle snapshots/2026-11-22T112639Z/attestation.sigstore.json \
  --repo HKUDS/AutoSwarm --signer-workflow HKUDS/AutoSwarm/.github/workflows/download-statistics.yml \
  --source-ref refs/heads/main --deny-self-hosted-runners
```

Collections listed with a commit predate signed snapshots; their figures are only recorded by that commit.

[Machine-readable history](history.json) · [Collector documentation](https://github.com/HKUDS/AutoSwarm/blob/main/docs/download-statistics.md)

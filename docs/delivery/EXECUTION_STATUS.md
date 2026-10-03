# HyLogger execution status — 2026-10-03

## Current local delivery

Analytical overview is implemented and running at http://127.0.0.1:8000, with
Compare, Map and Data & method. Shared QC, immutable response snapshots,
QC-versioned/active interval products, correct query similarity and explicit
model holdout are implemented. Target20 has 40 sensor products; the measured
50-hole batch has 95 products and 30,579 interval rows. Five TIR sources are
unavailable and 15 TIR inference rows are out of schema, explicitly reported.

59 tests passed, including isolated database integration cases. Live API/CSV
checks, real raw row/depth/median traces for two holes in both sensors, and a
browser walkthrough passed. Source-level lint is checked for the new delivery
modules; historical team scripts retain unrelated pre-existing lint issues.

Raw18 acquisition, the 200-hole gate, independent scientific/human review,
second-machine installation and hosted/client acceptance remain open. The
two-hole spectral pilot has limited transfer coverage and is not promoted.
See [current evidence](EVIDENCE_2026-10-03.md),
[contribution decisions](CONTRIBUTION_REVIEW.md),
[runbook](LOCAL_DELIVERY_RUNBOOK.md) and the compact
[system guide](../SYSTEM_EXPLAINED.md).

## Historical snapshot — 2026-09-25

Base `origin/main` `1cd4ffc`; isolated branch `codex/hylogger-system` at
`C:\Users\DCCN9\.codex\worktrees\hylogger-data-crunch-system`. Original `main`
was clean at `3ba3337` before work and was not modified. No applicable
`AGENTS.md` was found in the repository or its ancestors. This status separates
local engineering, scientific acceptance, and publication.

| Phase / task | Status | Evidence | Pending decision | Next action |
|---|---|---|---|---|
| F0 inventory, provenance and isolation | verificado | Git remote/branch/worktree inspection; 26 compatible processed Parquets (52,880,106 bytes), five extracted raw directories; Docker 29.7.2; team modules inspected | None | Retain source snapshots and branch history |
| F1 row alignment, sentinels, deduplication, masks and reports | verificado | `tests/test_system.py`; real raw SWIR/TIR 05GJD001 and LD82625W1; processed QC in `data/system/etl/*/qc.json`; source rows retained | Processed NVCL logs omit Final Mask; raw mask policy differs | Acquire explicit mask or keep processed limitation visible |
| F2 PostGIS, versioned intervals, API and restore | verificado locally | additive migration `002_interval_system.sql`; 1,897 catalogue holes, 11,475 intervals and 50 products in local DB; same source IDs on repeat; API trace and isolated backup/restore rehearsal | Dataset revision and interval width are identified; ETL algorithm revision is not yet part of interval-set key | Introduce active-set/version migration before changing aggregation semantics |
| F3 reproducible clustering | verificado as engineering evaluation | separate SWIR/TIR frozen K-Means cards and whole-hole test; direct frequency/PCA comparisons; HDBSCAN sample comparison; two-hole spectral PCA pilot; exact artifacts in `data/system/models`, `data/system/spectral` and `data/system/hdbscan_*.json` | New independent holes and assays for scientific selection | Review model cards; run confirmation on untouched data |
| F3b whole-hole comparison | implemented locally, exploratory | fixed 20-hole cohort, one row per hole; SWIR 20/20 and TIR 18/20; comparison cards and API/UI artifacts in `data/system/comparisons` | Independent hole cohort and geological interpretation | Review candidates against QC, geography, lithology and held-out holes |
| F4 geological validation and anomalies | bloqueado externamente | `docs/delivery/VALIDATION_DOSSIER.md`; 0.5/1/2 m sensitivity on two holes; spectral out-of-distribution flags; 20-hole relative outlier screen | Assays/lithology with IDs, units, depth overlap and GSWA review absent | Align external references and seek domain review after authorization |
| F5 connected English UI | verificado locally | Browser: map/search → 05GJD001 or LD82625W1 profile → detail → similar → spectrum → CSV; TIR empty state for 07THD003; processing status | Saved screenshots and human comprehension check pending | Capture shareable evidence and run five-question check with teammate |
| F6 incremental scale | implementado, capacity partially verified | 20-hole then 26-hole benchmarks; frozen one-hole inference with unchanged active model; resource table in `docs/delivery/EVIDENCE_2026-09-25.md` | Only 26 complete processed sources local; 50/200 and download capacity unmeasured | Acquire reviewed cohorts with size estimates; benchmark each gate |
| F7 local handover | implementado, partially verified | Compose starts API+PostGIS; generated data excluded from Git; guides, dictionary, model cards; isolated restore rehearsal | Clean installation on second machine, redistribution licence and external access unverified | Reproduce from acquired sources on clean machine; decide deployment separately |

`data/system` is ignored by Git and contains generated artifacts, benchmark
reports and the local database dump. The source scripts from Jovan, Avantika and
the SWIR workstream remain in place. Sathi's `quality.py` and `intervals.py` were
recovered from commit `37fac20`, reviewed and adapted. No branch was pushed,
merged or deployed.

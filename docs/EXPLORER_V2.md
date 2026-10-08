# HyLogger Explorer v2

A local application for statewide HyLogger exploration: clustering at interval, domain and whole-hole level (processed TSG logs and raw spectra), anomaly ranking, similarity between any number of holes, expected mineralogy at a location, and in-app download/processing of holes from the NVCL API and NCI THREDDS.

> **Data licence.** The GSWA *HyLogger Drillholes Package – Interim Data* (SQLite) is not QA/QC'd and must not be shared with third parties, published or displayed where third parties can access it. It lives only in the data root (`C:\HyLoggerData\reference`), never in Git. Run the application locally; do not host it publicly while it contains package data.

## Run it

```powershell
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r platform/requirements.lock -e .
copy platform\.env.example platform\.env       # set HYLOGGER_DATA_HOST (and optional HYLOGGER_LEGACY_HOST)
docker compose -f platform/docker-compose.yml up -d --build      # db (5434) + api (8100)
powershell -ExecutionPolicy Bypass -File scripts/start_worker.ps1 # background worker on the host
start http://127.0.0.1:8100
```

Compose project `hylogger_v2` runs `db` (PostGIS, port 5434) and `api` (port 8100); it does not touch the earlier `hylogger_system` stack (8000/5433). On Windows the worker runs on the host: SQLite's WAL download manifest fails across Docker bind mounts ("disk I/O error"). On Linux/macOS `docker compose --profile docker-worker up -d` runs it in a container instead. Jobs queued from the UI wait until a worker is running. First-time setup on an empty data root, from the **Data** tab or the command line:

```powershell
python -m hylogger.jobs submit reference_load              # GSWA package -> ref_* tables + statewide register
python -m hylogger.jobs submit catalog_refresh             # NVCL + THREDDS catalogue (optional; a catalogue snapshot is enough)
python scripts/bulk_fetch_processed.py --data-dir C:/HyLoggerData --workers 4   # one-off statewide download
python -m hylogger.jobs submit prepare                     # all downloaded holes -> mineral intervals
python -m hylogger.jobs submit analysis_refresh '{"mode": "retrain", "promote": true}'
python -m hylogger.jobs list
```

The host venv and the API image share the exact versions in `platform/requirements.lock`, so models written by the worker load in the API.

## What each tab answers

| Tab | Question | Content |
|---|---|---|
| Map | What exists, where, and what is it like? | All registered holes (NVCL catalogue ∪ GSWA index), coloured by data availability, hole cluster (choose 4–20), unusualness, exploration-target commodity, tectonic region, assays, or any analyte above a threshold (e.g. U > 100 ppm); rectangle selection |
| Hole | What changes down this hole? | Strip log on one depth scale: SWIR/TIR/VNIR mineral-group mix, interval cluster, domains, unusualness, raw Al-OH 2200 nm position and spectral class, assays, logged geology; interval detail with raw spectrum and most similar intervals elsewhere |
| Similarity | Which holes resemble each other? | N×N distance matrix (dendrogram order), t-SNE map of every hole's fingerprint, nearest matches with reasons; processed SWIR/TIR/combined and raw NIR/TIR fingerprints |
| Clusters | What are the recurring mineral assemblages? | Fine clusters nested in broad groups, mineral mix, median assay chemistry per cluster, mineral↔oxide correlations, η² by analyte, K selection, raw spectral classes |
| Anomalies | What should a geologist look at? | Ranked intervals with reasons, most unusual holes, data-quality outliers kept separate from geology |
| Prediction | What should I expect here? | IDW of the nearest holes' composition, ± spread, by true-vertical-depth band, with leave-hole-out and leave-district-out validation, an explicit support limit and the tested covariate model |
| Data | Can I add holes? | Catalogue search, selection, size/time estimate, jobs: fetch processed logs, fetch raw spectra (spectra-only or full ZIP, optional summary mode), re-run analysis, refresh catalogue, reload package |
| Method | How was this made? | Plain-language method, active run cards, credits |

## How it works

```text
NVCL API (per-log requests) ─► downloads/processed ─┐
NCI THREDDS (byte ranges)   ─► downloads/raw ─► raw summaries (1 m medians, band features)
GSWA SQLite package          ─► ref_* tables, explorer_hole register, assays, geology, surveys
                                                    ▼
             mineral interval features (SWIR/TIR/VNIR) ─► PostGIS interval_feature
                                                    ▼
   staged analysis runs: clusters · domains · anomalies · fingerprints · similarity · prediction
                                                    ▼  promote
                FastAPI (read + enqueue) ─► Explorer UI ◄─ worker (jobs queue)
```

1. **Acquisition.** `processed.fetch_processed_hole` now downloads one log per request and joins columns only after verifying identical depth rows (the WA server returns HTTP 502 for multi-log requests). Raw packages use `explorer.spectral_fetch`: the ZIP central directory and only the spectral members are read with explicit byte ranges and parallel parts; `zipfile` checks CRC-32. LD82625W1: 336 MB transferred of a 2.56 GB ZIP, all nine members byte-identical to the full download.
2. **Reference package.** `explorer.reference` loads all nine package tables (−999 → NULL; en dash restored), builds `explorer_hole` by normalised name matching (2,144 of 2,161 holes linked to the index, 1,482 to a WAMEX collar) and 5 km districts.
3. **Interval features.** `explorer.features` applies the shared reading contract, then Sathi's abundance table: each reading's Min1–3 weights are normalised and assigned to minerals; a 1 m interval's abundance is the mean over its diagnostic readings. NOTAROK components (marker pen, core tray, wood, vegetation) are excluded and counted as `not_rock`. Abundance is a relative interpretation weight, not mass percent.
4. **Clusters and domains.** Hellinger geometry, hole-balanced training, fine K by Sathi's rule, broad groups from a Ward merge of centroids, names from the mean mix; domains by penalised optimal segmentation.
5. **Anomalies.** Mean percentile of poor fit (Sathi), Isolation Forest and ±5 m contrast, plus rare-mineral flags; hole-level unusualness and data-quality outliers separately.
6. **Fingerprints and similarity.** Share of intervals per fine cluster, JS distance (Sathi); combined SWIR+TIR; raw whole-hole distance as variance-weighted Wasserstein between PCA score distributions (Yeshwanth), on mask-admitted 1 m medians. Hole clusters by average linkage, named from their mean mineral mix.
7. **Validation.** Assays are joined by depth overlap and harmonised to oxides; correlations and per-cluster chemistry are stored with each run.
8. **Prediction.** Inverse-distance weighting of the 8 nearest holes; leave-one-hole-out and leave-one-district-out cross-validation against the tectonic-region average; error by distance defines the support limit shown in the app (skill ≥ 0.15). A gradient-boosting model with location, tectonic region and IDW-from-other-districts features was tested with whole districts held out; it did not beat IDW, so it is reported but not used. Depth bands use minimum-curvature true vertical depth (`explorer/desurvey.py`) where surveys exist.
9. **Virtual assays.** Gradient boosting predicts chemistry from SWIR+TIR mineralogy per 1 m bin (`explorer/virtual_assay.py`), scored on whole holes held out. Analytes with R² ≥ 0.3 are shown as predicted tracks; their out-of-fold residuals add a *chemistry mismatch* anomaly component. Uranium, sulphur, Fe₂O₃ and SiO₂ are not predictable from HyLogger mineralogy and are not offered.
10. **Robustness.** The `robustness_check` job withholds ~20% of districts, refits the clusters and reports agreement (ARI) for the withheld intervals, and re-prepares a sample of holes at 0.5 m and 2 m to compare hole-similarity matrices with the 1 m result.

Every analysis writes a new `analysis_run` (staged) and is promoted explicitly, so retraining never silently changes what users see. Products (models, distance matrices, raw summaries) live under `<data root>/products`.

## Code map

| Area | Files |
|---|---|
| Reference and register | `src/hylogger/explorer/reference.py`, `db.py` |
| Features and loading | `explorer/features.py`, `prepare.py`, `data.py` |
| Analysis | `explorer/clusters.py`, `domains.py`, `anomaly.py`, `fingerprints.py`, `rawcluster.py`, `chemistry.py`, `validation.py`, `prediction.py`, `desurvey.py`, `virtual_assay.py`, `robustness.py`, `analysis.py` (orchestrator, staging, promotion) |
| Raw spectra | `explorer/spectral_fetch.py`, `raw_summary.py` |
| Jobs | `src/hylogger/jobs/queue.py`, `worker.py`, `step.py`, `__main__.py`; `platform/db/migrations/004_jobs.sql` |
| API and UI | `platform/backend/main.py` (v2), `backend/legacy.py` (v1 under `/legacy`), `platform/frontend/explorer.html`, `explorer.css`, `js/` |
| Database | `platform/db/migrate.py`, `migrations/005_explorer.sql` |

## Verification

```powershell
$env:HYLOGGER_TEST_DATABASE_URL = 'postgresql://hylogger:hylogger@127.0.0.1:5434/hylogger'
python -m pytest -q
```

Tests cover per-log joining and misaligned depths, mineral abundance and QC reconciliation (including not-rock and numeric codes), dataset selection, K rule and naming, synthetic cluster recovery and frozen assignment, segmentation, JS/Wasserstein distances, planted anomalies, prediction skill on a smooth field, spectral-only range fetch against a THREDDS-like server (no suffix ranges, imagery never transferred), the job queue (claim, cancel, stale requeue) and the v1 API in isolated schemas.

See [EXPLORER_V2_EVIDENCE.md](delivery/EXPLORER_V2_EVIDENCE.md) for measured coverage, validation and limits.

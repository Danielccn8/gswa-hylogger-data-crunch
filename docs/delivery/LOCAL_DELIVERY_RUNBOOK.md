# Local delivery runbook

For the short VS Code startup and presentation walkthrough, use [Quick start and demo](QUICK_START_DEMO.md).

Run commands from this repository root. Use its `.venv` with the project's analysis, platform and dev dependencies installed. The two Docker services belong to Compose project `hylogger_system`; unrelated containers are outside this workflow.

## Open the existing local delivery

```powershell
docker compose -p hylogger_system -f platform/docker-compose.yml up -d --build
Invoke-RestMethod 'http://127.0.0.1:8000/api/health'
```

Open http://127.0.0.1:8000. Overview defaults to the selected20 processed cohort. Choose **All prepared holes** for the 50-hole checkpoint. The app requires the local database; only basemap tiles require internet. The source/model artifacts and database volume are local and are not bundled in Git.

For a database created before this delivery, apply the additive active-set migration before opening the dashboard (the ETL command below also applies it):

```powershell
Get-Content -Raw platform/db/migrations/003_active_interval_sets.sql |
  docker exec -i hylogger_system-db-1 psql -U hylogger -d hylogger -v ON_ERROR_STOP=1
```

The migration can select an unambiguous legacy revision. Re-run ETL to generate the new QC-versioned products and dashboard summaries; merely applying the migration does not recreate summaries or models.

## Acquire and reproduce target20

Set `$sourceData` to the directory containing `catalog` and `downloads`. On the current machine it is the primary checkout's `data` directory; source files are not copied into the worktree. Keep this directory choice explicit, especially outside OneDrive for later large raw downloads.

```powershell
$sourceData = 'C:\Users\DCCN9\OneDrive - UWA\Data Science\4-Semester July-November 2026\Capstone\Data Crunch\data'
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
.\.venv\Scripts\python.exe scripts/prepare_delivery.py --data-dir $sourceData --output data/delivery-20261003 --fetch
$cohort = Get-Content configs/delivery_cohort.json -Raw | ConvertFrom-Json
$holes = @($cohort.regions.PSObject.Properties | ForEach-Object { $_.Value })
.\.venv\Scripts\python.exe platform/etl/run_system.py --data-dir $sourceData --holes @holes --test-holes @($cohort.test_holes) --output data/delivery-20261003
```

The acquisition script reuses existing selected tables and records failures. It does not resolve ambiguous datasets or bulk-download raw ZIPs. Review `cohort_inventory.json` before interpreting results. A fresh data root also needs the catalogue acquired and loaded with `platform/etl/load_holes.py` for map coordinates.

Reproduce the bounded raw pilot only when both packages are already extracted:

```powershell
.\.venv\Scripts\python.exe scripts/run_spectral_pilot.py --data-dir $sourceData --holes LD82625W1 WKDD15001 --region swir --output data/delivery-20261003/spectral/swir
.\.venv\Scripts\python.exe scripts/run_spectral_pilot.py --data-dir $sourceData --holes LD82625W1 WKDD15001 --region tir --output data/delivery-20261003/spectral/tir
```

Compose mounts this delivery's spectral directory. Treat the NIR/SWIR result as a 380–2500 nm package pilot, not a SWIR-only or statewide model.

## Verify and extend

```powershell
$env:HYLOGGER_TEST_DATABASE_URL = 'postgresql://hylogger:hylogger@127.0.0.1:5433/hylogger'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe scripts/verify_delivery.py --data-dir $sourceData
```

Database tests use unique schemas inside rolled-back transactions. Browser acceptance is `node scripts/check_dashboard.cjs` with Playwright available. Set `PLAYWRIGHT_MODULE` to its module path if it is installed outside the project; `BROWSER_CHANNEL` defaults to `msedge`. Screenshots and the result go to `data/delivery-20261003/browser`.

For additional compatible holes, run the ETL with `--mode infer`, the same output root and explicit `--infer-model-swir`/`--infer-model-tir` IDs from the model cards. This preserves the active models and reports unseen groups. Save the requested cohort before running. Inspect batch failures and source revisions before increasing the batch size. The 200-hole gate is not yet measured.

Stop only these services with `docker compose -p hylogger_system -f platform/docker-compose.yml stop`. Do not use volume deletion to fix a startup problem: the volume contains source histories and model assignments.

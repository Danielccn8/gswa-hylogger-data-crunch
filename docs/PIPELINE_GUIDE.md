# Pipeline guide

```text
NVCL processed Parquet / extracted TSG -> row QC -> depth intervals -> Parquet + QC JSON
                                                         -> PostGIS -> FastAPI -> map/profile/CSV
                                                         -> frozen model -> assignments
```

`platform/etl/run_system.py` reads one reviewed dataset revision per hole. Each input row receives one primary reason: admitted, invalid depth, unknown mask, non-rock, or nondiagnostic group. The counts reconcile to all source rows. Repeated depths remain separate source rows. For each 1 m bin, the feature vector is the frequency of diagnostic TSG group names among admitted readings. `n_total` includes other QC states at valid depths; `n_admitted` is the feature denominator. The observed source-depth minimum and maximum are retained; bin width is not observed-core coverage.

The set ID derives from the file checksum, hole, region and interval width. Rerunning identical inputs produces the same ID and database keys. A changed file or width creates a new set. Generated Parquet and QC JSON live under `data/system/etl/<interval_set_id>` and should stay outside Git. Load uses one transaction per complete set. Model IDs identify a frozen feature schema and K-Means run; the active model is explicit in PostGIS.

### One trace from source to screen

The acquired 05GJD001 SWIR processed log, dataset
`8a294cfc-68df-4f03-995b-c5fd0d47ab0`, has checksum
`f3079d58fe6de80aadd6034b5f2e660eea90bff0cb78a69d4131872010909cda`.
Its 150–151 m bin is interval `bbadb96c5a5b14505088e700` in set
`60df8d254d8440036ce3`. Before QC the bin contains 169 readings from
150.00000–150.99402 m, each with its own `source_row`. After QC, 56 diagnostic
TSG readings contribute features; 113 are nondiagnostic. The diagnostic
frequencies are DARK-MICA 0.625, CHLORITE 0.286 and CARBONATE 0.089. These are
shares of **56 interpreted readings**, not mineral mass percentages. The
processed log has no Final Mask, so its mask status is unavailable. The local
model assigns group 0 at transformed-centroid distance 0.803. Find the same
IDs in `data/system/etl/60df8d254d8440036ce3/intervals.parquet`, PostGIS
`interval_product`, `/api/system/intervals/bbadb96c5a5b14505088e700`, the
150–151 m profile row, and the exported CSV.

Raw adjusted spectra use a different policy: metadata and spectra are paired by
`source_row`, nonfinite/sentinel values are removed together, and Final Mask
must admit the reading. Their QC cannot be substituted for processed-log QC.

Example to reproduce on locally acquired data (never changes the source files):

```powershell
cd C:\path\to\hylogger-data-crunch-system
docker compose -p hylogger_system -f platform/docker-compose.yml up -d --build
.\.venv\Scripts\python.exe platform/etl/load_holes.py C:\path\to\acquired-data\catalog\catalog.csv
.\.venv\Scripts\python.exe platform/etl/run_system.py --data-dir C:\path\to\acquired-data --holes 05GJD001 05KCD001 07THD003 --output data/system --mode train
```

Open `http://localhost:8000`, select a map marker, then choose SWIR or TIR. To trace a plotted interval, open **How this was calculated** for dataset, source rows, interval set, feature frequencies, mask availability and model ID. The API exposes the same record at `/api/system/intervals/{interval_id}`. The CSV includes the selected model ID and source rows. Neither a TSG frequency nor a cluster number is a mineral mass percentage or geological classification.

To process a compatible new hole without retraining, use `--mode infer` with
both explicit `--infer-model-swir` and `--infer-model-tir` IDs from model cards.
The runner verifies region and dependency versions and leaves `active_model`
unchanged. An incompatible feature vocabulary is reported as a model-stage
failure. `--mode etl-only` prepares and loads intervals without assignment.
Never run the catalogue loader with `--prune` against an incomplete snapshot.

`docs/delivery/EVIDENCE_2026-09-25.md` records the measured local batch,
restore and browser checks; `EXECUTION_STATUS.md` gives the open gates.

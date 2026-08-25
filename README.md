# GSWA HyLogger Data Crunch

UWA CITS5553 Data Science Capstone, Group 7. This repository provides a reproducible path from GSWA/NVCL data discovery to clustering-ready HyLogger features.

The primary workflow is:

```text
Search the catalog -> preview processed TSG logs -> select holes
                   -> download processed logs and/or raw packages
                   -> inspect quality -> prepare features -> run clustering
```

## What `processed` and `raw` mean

| CLI term | Source | What it contains | Typical use |
|---|---|---|---|
| `processed` | NVCL Data Services through `nvcl-kit` | TSG-derived mineral, group, weight, error, mask, and diagnostic logs | Fast review and initial clustering |
| `raw` | NCI THREDDS ZIP package | NIR/SWIR/TIR spectra, TSG headers, images, CRAS, lidar, and supporting files | Spectral feature engineering and validation |

Processed logs are small and can be fetched individually. Raw data is published as one ZIP per hole; the downloader keeps the immutable ZIP and extracts only the requested local components.

## Repository map

| Path | Purpose |
|---|---|
| `src/hylogger/` | Installable acquisition, inspection, and preparation library |
| `configs/project.toml` | Service endpoints, download settings, and processed-log profiles |
| `configs/selections/` | Small, version-controlled cohorts of hole IDs |
| `data/` | Local catalog, downloads, manifests, and prepared data; ignored by Git |
| `notebooks/` | Team analysis organised by workflow stage |
| `experiments/` | Generated clustering runs and metrics; ignored by Git |
| `reports/` | Generated local HTML inspection reports |
| `tests/` | Offline fixtures, unit tests, and integration tests |
| `platform/` | Existing FastAPI/PostGIS/Leaflet consumer of prepared outputs |
| `docs/` | Data model, downloading, inspection, clustering, and team workflow guides |

## Installation

Python 3.11, 3.12, 3.13, or 3.14 is supported.

The repository includes `uv.lock`. If [uv](https://docs.astral.sh/uv/) is installed, the shortest reproducible setup is:

```powershell
uv sync --extra analysis --extra dev
uv run hylogger init
uv run hylogger doctor --online
```

The standard-library `venv` setup below remains fully supported when uv is unavailable.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[analysis,dev]"
.\.venv\Scripts\hylogger.exe init
.\.venv\Scripts\hylogger.exe doctor --online
```

Activate the environment if you prefer shorter commands:

```powershell
.\.venv\Scripts\Activate.ps1
hylogger doctor
```

If PowerShell blocks activation, keep using `.\.venv\Scripts\hylogger.exe`; activation is not required.

### macOS or Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[analysis,dev]'
.venv/bin/hylogger init
.venv/bin/hylogger doctor --online
```

Large datasets should normally live outside OneDrive. Copy `.env.example` to `.env` for reference and set the variable in your shell:

```powershell
$env:HYLOGGER_DATA_DIR = "D:\HyLoggerData"
```

The same setting can be applied to one command with `--data-dir` before the command name:

```powershell
hylogger --data-dir D:\HyLoggerData status
```

## Command quick start

### 1. Build the combined catalog

```powershell
hylogger catalog refresh
hylogger catalog search --raw --processed --limit 20
hylogger catalog search --operator "BHP" --raw
hylogger catalog show 05GJD001
```

`catalog refresh` combines the live NVCL borehole list with the NCI THREDDS raw-package catalog. It writes Parquet and CSV snapshots under `data/catalog/`.

### 2. Preview processed TSG data

```powershell
hylogger processed preview 05GJD001
hylogger processed preview 05GJD001 --contains "Min1"
```

Preview does not download raw spectra. It lists datasets, log names, spectral regions, and inferred TSG processing levels:

- `L1-system`: system-generated `s*` products.
- `L2-user`: user-refined `u*` products.
- `L3-domain`: domain-refined `d*` products.
- `unspecified`: logs without one of these naming markers.

### 3. Create a shared pilot selection

```powershell
hylogger selection create pilot --holes 05GJD001 --description "Initial cross-version pilot"
hylogger selection add pilot 05KCD001 05AMD001
hylogger selection show pilot
```

Commit `configs/selections/pilot.yaml`. Do not commit downloaded data.

### 4. Download processed logs

```powershell
hylogger processed fetch --selection pilot
hylogger processed fetch 05GJD001 --profile minerals
hylogger processed fetch 05GJD001 --all-logs
hylogger processed fetch 05GJD001 --log "Min1 uTSAS" --log "Wt1 uTSAS"
```

The default `clustering` profile is defined in `configs/project.toml`. Raw API responses are preserved as CSV and normalised copies are written as Parquet.

### 5. Check a raw download before starting it

```powershell
hylogger raw probe 05GJD001
hylogger raw fetch 05GJD001 --components spectra --dry-run
hylogger raw fetch --selection pilot --components spectra --dry-run
```

Always use `--dry-run` for a large selection before downloading.

### 6. Download or resume raw packages

```powershell
hylogger raw fetch 05GJD001 --components spectra
hylogger raw fetch --selection pilot --components spectra
hylogger raw fetch 05GJD001 --components spectra,images,cras
```

Available components are `spectra`, `images`, `cras`, `lidar`, `docs`, `all`, and `none`. The archive itself is always downloaded in full. If interrupted, run the same command again; the `.part` file is resumed when the server supports HTTP Range.

To register the existing team copy without duplicating it:

```powershell
hylogger raw import "..\data\05GJD001.zip" --mode reference --components spectra
```

Use `--mode copy` only when you intentionally want another full copy under `HYLOGGER_DATA_DIR`.
If the package is already extracted, register that directory without duplicating its contents:

```powershell
hylogger raw import "..\data\05GJD001.zip" --mode reference `
  --package-dir "..\data\05GJD001"
```

### 7. Inspect a hole

```powershell
hylogger inspect 05GJD001
hylogger inspect 05GJD001 --report
hylogger inspect 05GJD001 --report --open
```

The inspector works with processed data only, raw data only, or both. HTML reports are written to `reports/inspections/`.

### 8. Prepare clustering inputs

```powershell
hylogger prepare 05GJD001
hylogger prepare --selection pilot
hylogger status --selection pilot
```

Preparation writes:

- cleaned, depth-sorted sample tables to `data/prepared/samples/`;
- compressed NIR/TIR/MIR matrices to `data/prepared/spectra/` when raw data exists;
- one feature row per hole to `data/prepared/features/`;
- a combined `selection_<name>.parquet` feature matrix for a selection.

Start with `notebooks/30_clustering/01_baseline_clustering.ipynb` after preparing a pilot selection.

## Data safety and provenance

- Raw ZIP archives are immutable inputs and are never deleted automatically.
- Partial downloads use `.part` files and remain available after interruption.
- Completed raw downloads receive a SHA-256 checksum.
- Download state, source URL, response metadata, paths, and errors are stored in `data/manifests/downloads.sqlite`.
- TSG missing-value sentinels below `-1e38` are converted to missing values during preparation.
- Sample tables are sorted by depth before windowing or clustering.
- NIR/SWIR and TIR remain distinct spectral regions.

## Recommended team workflow

1. Agree on and commit one small selection YAML.
2. Preview processed logs before downloading raw packages.
3. Run raw downloads with `--dry-run` and check available disk space.
4. Run `inspect` and resolve quality warnings before feature engineering.
5. Put reusable logic in `src/hylogger/`, not in notebook cells.
6. Keep notebooks focused on questions, plots, and decisions.
7. Record model settings and random seeds under `configs/experiments/`.
8. Never commit client data, generated features, or model outputs.

Read [docs/TEAM_WORKFLOW.md](docs/TEAM_WORKFLOW.md) before opening the first data or modelling pull request.

## Known provider behaviour

The borehole catalogue and processed-log services are separate endpoints. One may remain available while the other is temporarily returning an error. Catalog snapshots and completed downloads are retained so the same command can be retried safely.

The authoritative libraries used by this project are:

- [AuScope nvcl-kit](https://github.com/AuScope/nvcl_kit)
- [GSWA pytsg](https://github.com/Geological-Survey-of-Western-Australia/pytsg)
- [NCI WA THREDDS catalog](https://thredds.nci.org.au/thredds/catalog/rs07/WA/catalog.html)

## Tests and code quality

```powershell
pytest
ruff check .
```

Network-dependent operations are not required for unit tests. Small fixtures simulate catalogs, scalar logs, and ZIP extraction.

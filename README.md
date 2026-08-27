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
uv sync --extra analysis --extra dev  # Create the environment and install analysis/development dependencies
uv run hylogger init  # Create the local project data and configuration folders
uv run hylogger doctor --online  # Check the installation, configuration, and online services
```

The standard-library `venv` setup below remains fully supported when uv is unavailable.

### Windows PowerShell

```powershell
python -m venv .venv  # Create an isolated Python environment in .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip  # Upgrade pip inside the environment
.\.venv\Scripts\python.exe -m pip install -e ".[analysis,dev]"  # Install this project and its analysis/development tools
.\.venv\Scripts\hylogger.exe init  # Create the local project data and configuration folders
.\.venv\Scripts\hylogger.exe doctor --online  # Check the installation, configuration, and online services
```

Activate the environment if you prefer shorter commands:

```powershell
.\.venv\Scripts\Activate.ps1  # Activate the environment for shorter commands
hylogger doctor  # Check the local installation and configuration
```

If PowerShell blocks activation, keep using `.\.venv\Scripts\hylogger.exe`; activation is not required.

### macOS or Linux

```bash
python3 -m venv .venv  # Create an isolated Python environment in .venv
.venv/bin/python -m pip install --upgrade pip  # Upgrade pip inside the environment
.venv/bin/python -m pip install -e '.[analysis,dev]'  # Install this project and its analysis/development tools
.venv/bin/hylogger init  # Create the local project data and configuration folders
.venv/bin/hylogger doctor --online  # Check the installation, configuration, and online services
```

Large datasets should normally live outside OneDrive. Copy `.env.example` to `.env` for reference and set the variable in your shell:

```powershell
$env:HYLOGGER_DATA_DIR = "D:\HyLoggerData"  # Store large downloads outside the repository and OneDrive
```

The same setting can be applied to one command with `--data-dir` before the command name:

```powershell
hylogger --data-dir D:\HyLoggerData status  # Use this data folder for this command and show download/preparation status
```

## Command quick start

### 1. Build the combined catalog

**How can we identify useful holes before downloading?**

```powershell
hylogger catalog refresh  # Rebuild the combined processed/raw hole catalog from live sources
hylogger catalog search --raw --processed --limit 20  # Show up to 20 holes available from both sources
hylogger catalog search --operator "BHP" --raw  # Find BHP holes with a raw package available
hylogger catalog show 05GJD001  # Show the complete catalog record for one hole
```

`catalog refresh` combines the live NVCL borehole list with the NCI THREDDS raw-package catalog. It writes Parquet and CSV snapshots under `data/catalog/`.

### 2. Preview processed TSG data

```powershell
hylogger processed preview 05GJD001  # List every processed dataset and scalar log for one hole
hylogger processed preview 05GJD001 --contains "Min1"  # Show only log names containing Min1
```

Preview does not download raw spectra. It lists datasets, log names, spectral regions, and inferred TSG processing levels:

- `L1-system`: system-generated `s*` products.
- `L2-user`: user-refined `u*` products.
- `L3-domain`: domain-refined `d*` products.
- `unspecified`: logs without one of these naming markers.

### 3. Create a shared pilot selection

```powershell
hylogger selection create pilot --holes 05GJD001 --description "Initial cross-version pilot"  # Create a reusable pilot cohort
hylogger selection add pilot 05KCD001 05AMD001  # Add two holes to the pilot cohort
hylogger selection show pilot  # Display the pilot cohort and its hole IDs
```

Commit `configs/selections/pilot.yaml`. Do not commit downloaded data.

### 4. Download processed logs

```powershell
hylogger processed fetch --selection pilot  # All pilot holes; default clustering profile
hylogger processed fetch 05GJD001 --profile minerals  # One hole; mineral, group, weight, and error logs
hylogger processed fetch 05GJD001 --all-logs  # One hole; every processed scalar log
hylogger processed fetch 05GJD001 --log "Min1 uTSAS" --log "Wt1 uTSAS"  # One hole; only these exact logs
```

The default `clustering` profile is defined in `configs/project.toml`. Raw API responses are preserved as CSV and normalised copies are written as Parquet.

### 5. Check a raw download before starting it

```powershell
hylogger raw probe 05GJD001  # Check the raw package URL, size, and resume support
hylogger raw fetch 05GJD001 --components spectra --dry-run  # Estimate one spectral download without starting it
hylogger raw fetch --selection pilot --components spectra --dry-run  # Estimate spectral downloads for every pilot hole
```

Always use `--dry-run` for a large selection before downloading.

### 6. Download or resume raw packages

```powershell
hylogger raw fetch 05GJD001 --components spectra  # Download one raw package and extract its spectra
hylogger raw fetch --selection pilot --components spectra  # Download/resume spectra packages for all pilot holes
hylogger raw fetch 05GJD001 --components spectra,images,cras  # Extract spectra, imagery, and CRAS data from one package
```

Available components are `spectra`, `images`, `cras`, `lidar`, `docs`, `all`, and `none`. The archive itself is always downloaded in full. If interrupted, run the same command again; the `.part` file is resumed when the server supports HTTP Range.

To register the existing team copy without duplicating it:

```powershell
hylogger raw import "..\data\05GJD001.zip" --mode reference --components spectra  # Register and extract spectra without copying the ZIP
```

Use `--mode copy` only when you intentionally want another full copy under `HYLOGGER_DATA_DIR`.
If the package is already extracted, register that directory without duplicating its contents:

```powershell
hylogger raw import "..\data\05GJD001.zip" --mode reference --package-dir "..\data\05GJD001"  # Register an already-extracted package without copying it
```

### 7. Inspect a hole

```powershell
hylogger inspect 05GJD001  # Print a quality and availability summary for one hole
hylogger inspect 05GJD001 --report  # Also generate an HTML inspection report
hylogger inspect 05GJD001 --report --open  # Generate the report and open it in the browser
```

The inspector works with processed data only, raw data only, or both. HTML reports are written to `reports/inspections/`.

### 8. Prepare clustering inputs

```powershell
hylogger prepare 05GJD001 --source processed  # Prepare downloaded API logs, even when a raw package exists
hylogger prepare 05GJD001 --source raw  # Prepare only an extracted raw package; no processed fallback
hylogger prepare --selection tsg_pilot --source processed  # Combine the five-hole processed test cohort
hylogger status --selection pilot  # Show download and preparation status for the pilot cohort
```

Preparation writes:

- cleaned, depth-sorted sample tables to `data/prepared/samples/`;
- compressed NIR/TIR/MIR matrices to `data/prepared/spectra/` when raw data exists;
- one feature row per hole to `data/prepared/features/processed/` or `data/prepared/features/raw/`;
- a combined `selection_<name>.parquet` feature matrix in the matching source folder;
- source-specific QC files including valid, missing and invalid category-label counts.

Omitting `--source` retains the legacy `auto` rule (raw first, otherwise processed).
Use an explicit source for experiments. A mixed-source selection or failed hole stops
matrix generation. Missing logs remain unknown; only unobserved categories in a
verified populated log can be filled with zero. Multiple processed datasets for one
hole are rejected pending an explicit dataset-selection policy.

Older prepared files are left in place but are not read by the updated baseline.
Re-run preparation to generate the source-specific files.

For the first small processed TSG test, follow [the step-by-step guide](docs/TSG_BASELINE_FIRST_TEST.md)
and open `notebooks/30_clustering/01_baseline_clustering.ipynb`. It clusters whole-hole
group-label proportions, not raw spectra or individual depths.

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
pytest  # Run the automated test suite
ruff check .  # Check Python code style and common errors
```

Network-dependent operations are not required for unit tests. Small fixtures simulate catalogs, scalar logs, and ZIP extraction.

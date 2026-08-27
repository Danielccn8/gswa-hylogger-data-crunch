# First processed TSG baseline test

## What are we testing?

Question: **Which holes have similar proportions of primary SWIR mineral-group labels?**

One model row represents one whole hole, not a depth interval or an individual spectrum.
For this first run, use `Grp1 uTSAS` only. Every hole must have usable observations of that
same log. This avoids mixing SWIR/TIR, system/user interpretations, and mineral/group
features that encode overlapping information.

The model is StandardScaler + optional PCA + K-Means. Start with two clusters and
PCA disabled. Set `PCA_VARIANCE = 0.95` later to experiment with PCA before K-Means.
PCA is dimension reduction, not a clustering algorithm. The original baseline did not
use PCA either. Parameters in the notebook settings cell control the run.

## The selected holes

Live scalar-log inventories checked on **27 August 2026** returned one dataset with
`Grp1 uTSAS` and `Min1 uTSAS` for each of these holes:

| Hole | Why include it in the first test? | Local state before this work |
|---|---|---|
| 05GJD001 | Familiar reference hole from our earlier exploration | Logs already downloaded |
| 05KCD001 | A nearby hole from the same operator, according to the 25 August catalogue snapshot | Logs already downloaded |
| MPWD10 | Checks the hole that previously exposed raw-versus-processed confusion | Logs already downloaded |
| 07THD003 | Another project with matching user-level SWIR logs | Inventory only |
| 09RTD001 | Matching SWIR logs despite different additional spectral products | Inventory only |

Only the two missing processed tables were downloaded during validation. Existing
all-log downloads were preserved. No additional raw ZIPs were downloaded.

`17BBDD004` and `NDD17001` were not selected: their dataset entries returned no scalar
logs in this live check. That is not proof they lack useful raw spectra or will never
have published logs.

This is a five-hole **convenience sample for checking the workflow**. Matching log
names do not establish matching scan generations, processing histories or geology.
Five holes are not enough to validate a geological classification or gold-targeting model.

## 1. Open PowerShell in the project

Use the repository folder containing `pyproject.toml`, `src`, `configs` and `.venv`.
The commands below do not require environment activation or `uv`.

```powershell
.\.venv\Scripts\hylogger.exe selection show tsg_pilot  # Show the five selected holes
.\.venv\Scripts\hylogger.exe prepare --help  # Confirm the new --source option is available
```

The selection is already provided in `configs/selections/tsg_pilot.yaml`.

## 2. Download the processed logs, if needed

The current local pilot already has all five processed tables. You can skip this step.
To reproduce the complete download on a fresh checkout:

```powershell
.\.venv\Scripts\hylogger.exe processed fetch --selection tsg_pilot --profile tsg_swir  # Fetch only user-level SWIR interpretation logs
```

The `tsg_swir` profile matches `* uTSAS`. Previewing is a cheap availability check:

```powershell
.\.venv\Scripts\hylogger.exe processed preview 07THD003 --contains uTSAS  # List available user-level SWIR logs, without raw spectra
```

Important: processed fetch currently replaces the local per-dataset table; it does
not merge new columns into an older all-log table. Do not re-fetch a narrow profile
over an existing all-log download unless that is intentional. For this pilot we
fetched only the two previously missing tables.

## 3. Prepare only processed data

```powershell
.\.venv\Scripts\hylogger.exe prepare --selection tsg_pilot --source processed  # Make one processed feature row per hole, then combine them
```

Look for `source: processed` for all five holes and the final matrix:

```text
data/prepared/features/processed/selection_tsg_pilot.parquet
```

Raw packages, even if already downloaded, are ignored by this command. Raw and processed
features and QC are saved separately. Legacy feature files remain untouched.

If any hole fails, fix that error and repeat preparation. No incomplete selection matrix
is published, and the notebook blocks an older matrix after a failed or interrupted
selection preparation. Changing the selection or re-preparing one hole also requires
rebuilding the selection matrix.

## 4. Open and run the notebook

Open `notebooks/30_clustering/01_baseline_clustering.ipynb`.
Choose **HyLogger Data Crunch (Python 3.11)** as the kernel.

If that kernel is missing, register the existing project environment once:

```powershell
.\.venv\Scripts\python.exe -m ipykernel install --user --name hylogger-data-crunch --display-name "HyLogger Data Crunch (Python 3.11)"  # Register the notebook kernel
```

For a fresh environment, install the project analysis dependencies first:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[analysis]"  # Install notebook, plotting and clustering dependencies
```

Keep these settings for the first run:

```python
SELECTION = "tsg_pilot"       # Five holes defined in the selection YAML
FEATURE_LOG = "grp1_u_tsas"   # Compare the same SWIR group log for every hole
N_CLUSTERS = 2               # Two exploratory groups, not a claimed geological truth
RANDOM_STATE = 42            # Reproducible model initialisation
PCA_VARIANCE = None          # No PCA in this first run; 0.95 enables it later
```

Run cells from top to bottom. The notebook shows:

1. Valid, missing and invalid label counts and percentages for every hole.
2. A small group-proportion table and a stacked bar chart, one bar per hole.
3. The cluster assigned to each hole, cluster sizes, and the silhouette score.
4. A new timestamped output folder under `experiments/tsg_baseline/`.

## How to read the result

If a hole has 40% chlorite, that means 40% of its **valid primary group labels** are
chlorite. It is not 40% chlorite by mass, volume or necessarily logged depth. Missing
and invalid/aspectral/not-a-rock readings are excluded from that denominator and
reported separately. No additional weight, error or scanner-mask threshold is applied
in this first baseline.

Zero is allowed only for a category not observed in an otherwise populated log.
A missing or entirely invalid log blocks clustering rather than becoming an all-zero row.
Constant columns are removed; quality counts and provenance are never model inputs.
Standard scaling gives each varying group comparable influence and can amplify rare groups.

Cluster IDs 0 and 1 are arbitrary names. They do not mean good/bad, gold/no gold, or
low/high mineral quality. A silhouette score is an exploratory separation diagnostic,
not evidence of geological validity, particularly with five holes or singleton clusters.
The model requires at least three holes and `2 <= N_CLUSTERS < number of holes`.

The output folder contains `assignments.csv`, `proportions.csv`, `quality.csv`, and
`run.json` with parameters, feature names, input checksum and library versions.
Generated data and runs remain ignored by Git.

The next discussion should be about the **five profiles and their quality table**,
not about interpreting the clusters as mineral discoveries.

Technical references: [scikit-learn silhouette score](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.silhouette_score.html)
and [PCA](https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.PCA.html).

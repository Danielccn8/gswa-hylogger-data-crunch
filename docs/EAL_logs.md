# EAL001 — Processed Scalar Logs

Source: NVCL processed API, dataset `c2ac7a3d-63af-4a4f-aaa6-f2cf6dec1d6`
File: `scalar_logs.parquet` (20 logs, 27,136 rows)

## What's in this file

Two independent TSG classification methods, run on the same SWIR data, each
giving the top 3 mineral matches per depth:

- **`u_tsas`**: uTSAS, spectral library match
- **`uj_clst`**: ujCLST, cluster-based classification

These are **not ground truth**, they're two different proprietary methods
and can disagree (e.g. one row had `min1_u_tsas = Kaolinite-PX` while
`min1_uj_clst = Quartz` at the same depth).

## Columns

| Column | Description |
|---|---|
| `hole_id` | Borehole ID |
| `dataset_id` | NVCL dataset UUID |
| `start_depth`, `end_depth` | Depth interval (m) |
| `min1/2/3_u_tsas` | Ranked mineral matches (uTSAS) |
| `wt1/2/3_u_tsas` | Weight/proportion of each match (0–1) |
| `grp1/2/3_u_tsas` | Mineral group for each match |
| `error_u_tsas` | Fit error for uTSAS |
| `min1/2/3_uj_clst` | Ranked mineral matches (ujCLST) |
| `wt1/2/3_uj_clst` | Weight/proportion of each match (0–1) |
| `grp1/2/3_uj_clst` | Mineral group for each match |
| `error_uj_clst` | Fit error for ujCLST |

## Notes

- `error` scales differ between methods (not directly comparable).
- `NaN`s appear when a method has no call at that depth.
- This hole's processed logs are SWIR-only, no TIR scalar logs, though raw
  TIR spectra were downloaded separately.


# EAL001 — Raw Spectra

Source: raw THREDDS ZIP, processed via `hylogger prepare EAL001`

Files: `EAL001_nir.parquet` (175 cols), `EAL001_tir.parquet` (120 cols),
`EAL001_qc.json`, `EAL001.npz`

## The spectra (`EAL001.npz`)

| Array | Shape | Description |
|---|---|---|
| `nir_spectra` | (27136, 531) | Reflectance, 531 NIR wavelengths per row |
| `nir_wavelength` | (531,) | NIR wavelength values (nm) |
| `tir_spectra` | (27136, 341) | Reflectance, 341 TIR wavelengths per row |
| `tir_wavelength` | (341,) | TIR wavelength values (nm) |

## Sample tables — column groups

- **IDs/position**: `hole_id`, `depth_m`, `tray`, `section`, `sample` — where each row is
- **Absorption features (1–25)**: `depth{n}`, `centre{n}`, `width{n}` — detected spectral dips. Real values for NIR; **all zero for TIR** (needs follow-up)
- **Mineral scalars**: `min/wt/grp1-3_s_tsas` (NIR), `_sj_clst` (TIR) + `error` — system-level (`s`) mineral classification, a third method alongside `u_tsas`/`uj_clst` from the processed logs
- **pXRF concentrations** (NIR only): `p_xrf_{element}_concentration`, ~30 elements. Some values are `"<LOD"` (below detection limit)
- **QC/diagnostic**: `recovery_rate`, `hy_log_diag`, `snr_*`, etc.
- **Unused**: `rock_marks`, `cust_*` — all null for this hole

## QC summary

| | NIR | TIR |
|---|---|---|
| Rows | 27,136 | 27,136 |
| Depth range | 0.004–157.79 m | same |
| Duplicate depths | 1,358 | same |
| **Kept %** | **65.57%** | same |

## Planned PCA / clustering workflow

1. Filter duplicate-depth rows (keep `hy_log_diag == "Good"` where duplicated)
2. Null out TSG sentinel values (< -1e38) in the spectra
3. Preprocess: standardize wavelengths and/or continuum removal (TBD)
4. Run PCA **separately** on `nir_spectra` (531 wavelengths) and `tir_spectra`
   (341 wavelengths). Never combined, per supervisor guidance
5. Pick number of components from explained variance (see plot)
6. Cluster on the reduced components (not raw wavelengths)
7. Compare resulting clusters against TSG scalar labels (`u_tsas`/`uj_clst`/
   `s_tsas`) as a baseline, not as clustering input


## Quality flags (observed, not officially documented)

`hy_log_diag` and `final_mask` are TSG-internal quality indicators. Meanings
below are inferred from the data, not confirmed by documentation (verify
with supervisor if precision matters).

**`hy_log_diag`**: one or more space-separated flags per row (`Good`, `PZ`,
`AL`, `WC`, or combos like `AL PZ`). `Good` rows are far more likely to have
a mineral classification (66.6%) than any flagged row (18–46%).

**`final_mask`**: binary (0/1), appears to be TSG's own "usable row" gate:
- `final_mask == 0` → 100% have no mineral call (fully excluded)
- `final_mask == 1` → 97.8% have a mineral call

`final_mask` and `hy_log_diag` don't fully overlap, some `Good` rows are
still masked out (0), so something beyond the diagnostic flag decides the
mask.

**Filtering recommendation:** `final_mask == 1` is the cleaner filter for
PCA/clustering. It directly reflects whether TSG considered the row usable,
rather than relying on `hy_log_diag` alone.
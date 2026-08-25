# Data model

## Acquisition layers

### Catalog

One row per normalised hole ID. The table is an outer join of the NVCL borehole service and the NCI THREDDS raw catalog. `nvcl_available` and `raw_available` must be interpreted independently.

### Processed logs

Processed logs are downloaded per NVCL dataset. The original CSV response and log inventory are retained next to a normalised Parquet file.

The normalised table always adds `hole_id` and `dataset_id`. Provider column names are converted to snake case. The source log names and IDs remain in `logs.json`.

### Raw package

The raw layer stores the original ZIP plus selectively extracted components. A package may contain NIR/SWIR, TIR, MIR, core imagery, CRAS imagery, lidar/high-resolution depth data, TSG headers, calibration files, and processing configuration.

### Prepared samples and spectra

Prepared sample tables are cleaned and sorted by depth. Spectral regions remain separate so that incompatible bands or mineral families are not silently combined. NPZ files contain arrays named `<region>_spectra` and `<region>_wavelength`. NPZ is the pilot format; move to Zarr only when multi-hole access patterns justify it.

### Hole features

Feature files contain one row per hole. Category proportions are namespaced by spectral region and source column. Invalid categories such as `INVALID`, `Aspectral`, and `NotARok` are excluded from proportions but remain visible in quality-control reports.

## Required provenance

Every acquired artifact records its hole ID, kind, dataset ID when applicable, source URL, local path, status, byte count, checksum when applicable, service metadata, timestamps, and errors in `downloads.sqlite`.

## Invariants

- Source ZIPs are immutable.
- Missing sentinels below `-1e38` are not valid measurements.
- Depth order from a scanner is not assumed to be monotonic.
- Processing level and spectral region are explicit metadata, never interchangeable measurements.
- Not every NVCL hole has a public raw package, and not every raw catalog entry has a matching NVCL record.

# Local data workspace

This directory is created and managed by the `hylogger` CLI. Its contents are not committed to Git.

```text
data/
├── catalog/                 Combined NVCL and THREDDS snapshots
├── downloads/
│   ├── processed/<hole>/    Raw CSV responses and normalised Parquet logs
│   └── raw/<hole>/
│       ├── archive/         Immutable source ZIP
│       └── extracted/       Selected local components
├── prepared/
│   ├── samples/             Clean, depth-sorted scalar/sample tables
│   ├── spectra/             Compressed spectral matrices
│   └── features/            Hole-level clustering features
├── manifests/               SQLite acquisition/provenance manifest
└── cache/                   Regenerable temporary service data
```

Run `hylogger init` to create the layout. Use `HYLOGGER_DATA_DIR` to place it on a drive with enough capacity.

Never manually edit completed raw ZIP archives. If a download is interrupted, rerun the same `hylogger raw fetch` command.

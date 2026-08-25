# Team workflow

## Workstreams

### Acquisition and quality control

Own catalog reconciliation, processed-log profiles, raw downloads, manifests, provider failures, and data contracts.

### Features and modelling

Own clustering-ready features, baselines, experiment configuration, validation, interpretation, and anomaly methods.

### Explorer and platform

Own inspection presentation, API loading, PostGIS integration, maps, filtering, and the eventual interactive workflow.

Each workstream must deliver against the same committed pilot selection.

## Branch and review rules

- Never commit data, ZIPs, Parquet outputs, NPZ arrays, local HTML reports, or SQLite manifests.
- Commit cohort selections and experiment configurations.
- Put reusable transformations in `src/hylogger/` with offline tests.
- A notebook may call library functions; it should not become a second ETL implementation.
- Include the exact reproduction command in each pull request.
- Data-contract changes require acquisition and modelling review.

## Weekly vertical slice

At least one pilot hole should pass the full flow every week:

```text
catalog -> preview -> fetch -> inspect -> prepare -> cluster -> interpret
```

## File ownership

| Path | Primary owner | Review required from |
|---|---|---|
| `src/hylogger/catalog.py`, `processed.py`, `raw.py` | Acquisition | Modelling |
| `src/hylogger/prepare.py` | Modelling | Acquisition |
| `src/hylogger/inspect.py` | Explorer | Acquisition |
| `notebooks/30_clustering/` | Modelling | Another workstream |
| `platform/` | Explorer/platform | Acquisition for data-contract changes |

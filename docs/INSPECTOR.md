# Inspector guide

The inspector answers what data is available locally, what depth and spectral coverage it provides, and what should be done next.

```powershell
hylogger inspect 05GJD001
hylogger inspect 05GJD001 --report --open
```

The terminal view is for rapid triage. The static HTML report is for local sharing and quality-control decisions.

Processed summaries include shape, depth coverage, duplicates, backward steps where source ordering is still visible, and common mineral/group categories. Raw summaries include archive size, extracted package, spectral regions, readings, bands, wavelength range, and scalar-column counts.

The inspector does not call NVCL. Run `processed preview` when a live service inventory is required.

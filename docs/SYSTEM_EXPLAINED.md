# HyLogger system explained

> **8 October 2026:** the Explorer v2 application supersedes the dashboard described below for statewide work. See [Explorer v2](EXPLORER_V2.md). This page remains the record of the 3 October pilot.

This is the short guide to what the system does, how the data moves and why each step exists. Update it whenever the implementation changes. Keep it readable in one sitting; detailed evidence belongs in the linked technical records.

**Status: 3 October 2026.** Implemented locally on `codex/hylogger-system`, building on `14039dd`. The application runs at **http://127.0.0.1:8000**. The selected 20 holes now have both processed SWIR and TIR products. A separate 50-hole expansion produced 95 sensor products. See [delivery evidence](delivery/EVIDENCE_2026-10-03.md) and [the delivery plan](delivery/NEXT_DELIVERY_PLAN.md) for checks and remaining work.

## 1. What are we helping the user do?

A geoscientist should be able to understand the available data, find similar drill holes, inspect changes with depth and export the evidence. A cluster is a group of similar measurements or profiles. It is not automatically a rock type, an ore deposit or a discovery.

We work at two levels: **intervals** locate changes down a hole; **whole-hole profiles** help compare holes. The interface must always say which level it shows.

## 2. Are raw and processed TSG prepared in the same way?

They share quality and traceability rules, but need different preparation.

| | Spectra from a downloaded package | Processed TSG logs from the API |
|---|---|---|
| What we read | A spectrum across wavelengths, with depth and package metadata | TSG mineral/group interpretations, fit weights/errors and other available logs |
| Current location | `downloads/raw/<hole>/...` | `downloads/processed/<hole>/<dataset>/...` |
| Main checks | Correct pairing of rows, mask, missing values and compatible wavelengths | Correct labels, depth, missing values, available logs and dataset revision |
| Initial model row | A representative spectrum for a depth interval | Group-label frequencies within a depth interval |
| Important limitation | A package may contain adjusted TSG spectra, not uncorrected instrument data | A processed table may not contain Final Mask; its absence must remain visible |

Here, `raw` is a repository/download label. It does not prove that every file is an untouched instrumental measurement. SWIR and TIR are separate sensor families with different information; we do not add their reported proportions together as one rock composition.

## 3. How does data move through the system?

```text
NVCL API logs -----------------> processed source adapter ----+
                                                             |
Downloaded TSG spectra -------> spectral source adapter ------+
                                                             v
                                       identity, quality and source-row checks
                                                             v
                                       versioned intervals and quality reports
                                                             v
                                       features -> model -> assignments
                                                             v
                                       PostGIS + referenced artifact files
                                                             v
                                       FastAPI -> views -> filtered CSV
```

Source responses are preserved in checksum-addressed snapshots. A narrower API request does not replace a broader selected table. Large spectral arrays stay in files. The database stores metadata, interval products, precomputed summaries and model assignments. Opening a screen does not download or train anything.

## 4. What does preparation actually change?

1. **Identify the source.** Record the hole, dataset/revision, sensor and checksum. Assign each measurement its original `source_row`.
2. **Check each reading.** Recognise missing-value sentinels, check depth and apply the verified mask policy. Distinguish invalid measurements from valid spectra with no diagnostic mineral interpretation.
3. **Keep rows aligned.** Any filtering or sorting moves the measurement and its metadata together. Different readings at the same depth remain different readings.
4. **Build intervals.** The initial width is 1 m. For processed logs, calculate the relative frequency of diagnostic primary group labels. For the spectral pilot, calculate a median spectrum from eligible readings.
5. **Keep the explanation.** Save source rows, support, exclusions, policy and version beside each output. A 1 m bin is not proof of 1 m of recovered core, and an empty section is not zero mineral content.

`INVALID`, `Aspectral` and `NotARok` are not mineral categories. Their meanings differ and must remain distinguishable. A missing log is unknown. Zero is appropriate for an unobserved category only after confirming that its parent log is present and populated.

The active preparation paths now share `preparation.py`: finite, non-negative depths; paired source rows; finite spectra without TSG sentinels; and reconciled exclusion counts. Primary spectral work requires Final Mask. Processed logs without it remain usable with the limitation displayed. Historic notebooks are research references and do not define the application pipeline.

Each processed product includes QC version `reading-contract-v2` in its identity. `active_interval_set` selects one source/width/QC revision per hole and sensor. Old products remain accessible by exact ID; profiles and coverage no longer pool their rows.

## 5. Which code does the work?

Paths below are relative to this repository. These files already exist; planned replacements must be reflected here when implemented.

| Step | Code | Responsibility |
|---|---|---|
| Find and select holes | `src/hylogger/catalog.py`, `selections.py` | Catalogue and named hole lists |
| Acquire sources | `src/hylogger/processed.py`, `raw.py`, `manifest.py` | API logs, packages and acquisition records |
| Prepare reusable data | `src/hylogger/prepare.py` | Sample tables, arrays and whole-hole baseline features |
| Apply the common contract | `src/hylogger/preparation.py` | Paired source rows, depth/spectrum checks, mask eligibility and reconciled counts |
| Prepare processed intervals | `src/hylogger/system.py` | TSG group frequencies, source rows and QC reports |
| Prepare spectral intervals | `src/hylogger/intervals.py`, `quality.py` | Paired readings, wavelength checks, mask policy and aggregation |
| Fit processed interval models | `src/hylogger/interval_model.py` | Frozen feature schema, K-Means, assignments and model card |
| Run the spectral pilot | `scripts/run_spectral_pilot.py` -> `src/hylogger/spectral_experiment.py` | Current two-hole normalization/PCA/K-Means contrast |
| Compare whole holes | `scripts/compare_20_holes.py` -> `src/hylogger/hole_comparison.py` | Aggregate interval profiles and compare hole-level patterns |
| Summarise and select versions | `src/hylogger/dashboard.py`, `platform/db/migrations/003_active_interval_sets.sql` | Stored hole summaries and one active interval revision |
| Load and coordinate | `platform/etl/run_system.py` | Processed ETL, model fitting/frozen inference and batch records |
| Serve and display | `platform/backend/main.py` -> `platform/frontend/dashboard.html`, `app.js`, `app.css` | API, four workspaces and CSV export |

The processed runner and spectral pilot are separate today. A common preparation contract does not mean the processed runner already performs the spectral pipeline.

## 6. How does clustering work?

**Processed interval baseline.** Each row describes one interval's TSG group frequencies. The current model takes their square roots and applies K-Means separately to SWIR and TIR. K-Means finds centres and assigns each row to the nearest one. PCA is not part of this active processed baseline.

**Spectral pilot.** The current pilot uses 1 m median adjusted spectra, SNV normalization, band scaling, PCA and K-Means. PCA compresses wavelengths; K-Means forms groups. Fit on LD82625W1, then apply the frozen transformations to WKDD15001. The package's NIR section spans 380–2500 nm (VNIR + SWIR); TIR spans 6000–14500 nm. Of 318 inference intervals, 271 NIR/SWIR and 31 TIR intervals exceed the training distance threshold. That flags limited training coverage, not proven geological errors. The pilot is not promoted as a general WA model.

**Whole-hole comparison.** The current comparison averages diagnostic interval profiles with equal weight per interval, then compares those hole profiles. Its cluster IDs differ from interval-model IDs. This weighting reduces the influence of dense scanning between intervals; it does not resolve every support bias within intervals.

**Choosing and checking a model.** The delivery manifest freezes 14 Goldfields development holes and all six Eastern Pilbara/Paterson holes as a regional transfer holdout. K-Means gives each development hole equal total training weight; `k=2,3,4` is compared only on development data. The selected processed models use three SWIR groups and four TIR groups. Holdout silhouettes are 0.185 and 0.358. These are engineering transfer checks, not independent geological validation. The runner retains its old alphabetical fallback for callers that omit an explicit split; the delivery command supplies the manifest split.

**Adding data.** Frozen inference uses the existing feature order and centres. Retraining creates a new model version. The 50-hole batch reused the selected models; 15 TIR intervals with unseen groups were flagged `out_of_schema`, leaving compatible intervals usable. Distance is not a probability of mineralisation.

## 7. How will the user read the results?

Navigation: **Overview**, **Compare**, **Map**, **Data & method**. Overview shows descriptive statistics and original-unit profiles. Compare puts up to four holes on a common 0–100% scale. Map shows the same filtered holes. Data & method explains coverage, exclusions, model choices and batch failures. These screens do not classify whole holes using interval cluster IDs.

All views open the same hole/interval detail and share cohort, sensor and model context. Detailed IDs live under “How this was calculated”; the main screen leads with meaningful labels, units and evidence. Exports must match the visible filters and result version.

Similar intervals are now ranked by distance **to the selected interval**, using square-root frequencies across compatible active sources. They can belong to other holes or model groups. Profiles select one source revision; CSV exports pin that revision and model and use the applied depth range. A large profile is paginated. An export exceeding 10,000 intervals asks for a narrower depth range instead of silently truncating.

## 8. One real example

In the saved processed SWIR product for **05GJD001, 150-151 m**, 169 readings occur in the bin. Of these, 56 contribute diagnostic group labels and 113 are nondiagnostic. DARK-MICA accounts for 62.5% of the 56 diagnostic labels. This does not mean 62.5% of the rock's mass is dark mica. Final Mask is unavailable in that processed source.

The historical interval ID is `bbadb96c5a5b14505088e700`, in set `60df8d254d8440036ce3`. The current QC version creates a new set ID; this old example remains a traceable historical record. See [the pipeline guide](PIPELINE_GUIDE.md) for its source trace.

## 9. What exists and what is next?

The local candidate contains the connected dashboard, API, database, shared preparation, frozen interval models and separate spectral pilot. The target cohort is complete for processed SWIR/TIR. The wider cohort has 50 SWIR and 45 TIR products: 30,579 sensor-specific interval rows. Both raw sensors in LD82625W1 and WKDD15001 passed source-row/depth/median trace checks. The other 18 target raw packages are not yet acquired. The historical “team-20” comparison remains a different, separately labelled artifact.

Next: broaden spectral coverage in staged downloads, examine normalization and transfer before promoting a raw model, measure the 200-hole gate, and obtain independent geology/assay and user-comprehension checks. Clean-machine handover and any hosted/client deployment remain separate acceptance steps.

| Date | What changed | Why | Evidence |
|---|---|---|---|
| 2026-10-03 | Implemented shared QC, source snapshots, active revisions, explicit model split, dashboard and 50-hole frozen inference. | Make outputs explainable, preserve source history and connect useful views. | 59 tests including isolated database cases; two real raw holes/both sensors; API/CSV checks; browser acceptance. |
| 2026-10-03 | Added this guide and the next delivery plan. No pipeline or UI implementation changed. | Keep the next work understandable and reviewable. | Existing code, saved artifacts and the read-only project audit. |

For each implementation change, add one short row here and update the affected explanation above. Record **what, how, why and verification**. Keep only the latest few rows; retain detailed history in Git and [execution status](delivery/EXECUTION_STATUS.md).

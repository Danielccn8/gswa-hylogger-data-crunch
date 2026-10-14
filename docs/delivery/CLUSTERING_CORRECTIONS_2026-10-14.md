# HyLogger clustering correction audit — 14 October 2026

**Completed locally:** the reviewed 15-run analysis set is active at **http://127.0.0.1:8100**. It was promoted in one guarded transaction at 12:37:02 UTC, preserving the previous 15 runs and all products. Only the existing Docker API was restarted. No commit, push, remote download or deployment occurred.

Target repository: `C:\HyLogger\explorer-v2`, base/current HEAD `0c9e20d59bf1ebd33cbcbeb545b259c8b13ec645`. The OneDrive checkout was not the target. Preexisting desktop, data-recovery, job, reference and documentation edits were preserved.

## Corrected behaviour

- Combined processed and sequence views use the intersection of supported sensor cohorts. Every retained pair has both sensors' evidence. Per-sensor views retain holes outside that intersection; missing evidence is never assigned an invented maximum distance. Equal sensor weighting is explicit; sequence distances are not claimed to have calibrated equal scales.
- Interval modelling requires at least three diagnostic readings and a diagnostic fraction of 0.5, excludes primary-frequency and unknown representations by default, and records exclusion counts and 0.25/0.5/0.75 policy sensitivity. Descriptive intervals remain visible with null model labels where excluded. The fraction is a declared support policy, not a calibrated accuracy threshold. Missing masks remain unknown.
- Fine K selection requires valid, noncollapsed candidates and the minimum-share gate. Broad Ward grouping weights fine centroids by represented training counts. Unsupported models are reported explicitly.
- Raw whole-hole distances use versioned sliced Wasserstein over the joint unwhitened PCA score distribution, with axes plus seeded unit directions. The variance-weighted marginal legacy comparator is saved on the same cohort/PCA. Both raw fits met their 95% PCA variance target: NIR 8 components/0.9631; TIR 7/0.9536.
- Automatic hole groups now require at least two holes per group. NIR's former 1260/1 split fails this replication floor; all 1261 holes retain distances, embeddings, neighbours and anomaly scores, with **Unassigned — no supported hole grouping** instead of an accepted group count. This floor is not a calibrated accuracy threshold. Manual cuts use exact K even for tied heights and explicitly label unsupported exploratory cuts.
- Frozen interval schemas and cards identify the algorithm source, eligible data and feature schema. Ordinary schemas describe the full eligible cohort; K-Means still trains on a hole-balanced sample. Independent district checks learn schema and K on development districts and leave unseen held mineral mass unassigned.
- Robustness reports link to the exact interval run and provenance. Historical unlinked reports remain inspectable. UI policy, method, counts and virtual-assay summaries come from active cards. Automatic/manual grouping status is consistent across map, scatter and selected-hole table.

## Measured results

| Evidence | SWIR | TIR |
|---|---:|---:|
| Previous model intervals | 373,567 | 359,850 |
| New eligible model intervals | 289,266 | 353,059 |
| All descriptive intervals | 416,287 | 384,115 |
| Excluded from descriptive cohort | 127,021 | 31,056 |
| Previous fine K | 12 | 10 |
| New fine / broad K | 8 / 6 | 2 / 2 |
| Final minimum training cluster share | 0.04169 | 0.31903 |
| Supported processed fingerprint holes | 1,177 | 1,149 |

The processed combined view contains **1100** common holes from a union of 1226, excluding 126 from this view. Sequence combined contains **1106** common holes from a union of 1212, excluding 106. Finite, symmetric, zero-diagonal matrices and exact equal sensor means were checked. The initial processed view had 316 pairs with no shared sensor; the corrected view includes no such pairs.

The new raw metric and marginal comparator have same-cohort rank agreement rho 0.9277 (NIR) and 0.9414 (TIR). This describes agreement, not geological accuracy or an algorithm champion. Raw views retain 1261 NIR and 1225 TIR holes with at least ten finite intervals. NIR has 1260 geographically plottable holes; `WILSON CLIFFS 1_HL3` lacks latitude but remains in the embedding and distance view.

Three development-only district repeats selected SWIR K=10 (full-fit K=8) and TIR K=2. SWIR assigned all held intervals. TIR left 475–697 held intervals unassigned for unseen mineral mass (0.66–0.94%). No current accuracy or held-out ARI claim is made.

The **24-hole-per-sensor width pilot** found rho versus 1 m of SWIR 0.963/0.751 and TIR 0.987/0.977 at 0.5/2 m. The 2 m comparisons retained 20 SWIR and 23 TIR holes after the ten-interval floor. This suggests greater SWIR width sensitivity in this pilot; it is not a population-wide conclusion. All 48 selected source hashes matched active feature QC and were unchanged before/after the pilot.

## Verification and provenance

**Final full suite: PASS 132, SKIP 12**, two existing synthetic convergence warnings, 14.54 seconds. The skipped fixtures require a dedicated configured test database; the shared live local database was not used for those fixtures. Eleven integration correction tests pass, including empty/tiny combined cohorts, exact manual cuts with tied heights, unsupported groups, source/data drift and stale validation gating. Touched-source/new-test Ruff, frontend Node syntax and diff whitespace checks pass.

Live API and browser checks passed for both sensor cluster cards, current model-linked robustness, method, supported combined similarity, NIR automatic/manual unsupported grouping and a rendered pair heatmap. A support-excluded SWIR interval in `BEHARRA SPRINGS NORTH 1` remained visible with a null cluster label. Browser console errors were absent during the smoke check.

The main staged refresh took 946.2 seconds. Its original six core source hashes were unchanged. A separate validation follow-up preceded validation; later hole-group replication/metadata fixes produced eight new hole runs from exact saved distances. Distance matrices, embeddings, neighbours and anomaly scores were preserved; source and new NPZ checksums are recorded. The seven supported partitions were identical to their originals, so full-feature names were preserved; no names derive from renormalized truncated top-mineral summaries. The SWIR hole group with Chlorite-FeMg mean 0.49647 retains its correct mixed name.

Interval and robustness cards retain the exact source fingerprints consumed at fitting. Final source hashes explicitly record later grouping selection and metadata-only changes rather than retroactively replacing provenance. Copied raw interval-class/sequence-step artifacts represent interval classes, not hole grouping, and were intentionally preserved.

## Evidence and remaining limits

Evidence folder: `reports/audits/clustering-corrections-20261014/`. Start with `checks.json`, `api_acceptance.json`, `browser_acceptance.json`, `final_summary.json`, `source_frozen.json`, `source_final.json` and `promotion_receipt.json`. `promotion_manifest.json` and `rollback_reviewed.py` provide the guarded local rollback; all old products remain present.

Screenshots: `browser_clusters_swir.png`, `browser_clusters_tir.png`, `browser_method.png`, `browser_similarity_combined.png`, `browser_raw_nir_auto.png`, `browser_raw_nir_manual4.png`.

Processed mask coverage is unavailable in this snapshot; fit-error thresholds are not calibrated. Raw results are descriptive, not a deployable frozen raw-inference model. The raw input manifest is explicitly **post-run only** and does not prove pre-run file stability. The all-raw-products-missing edge still gives a generic unsupported-view report. Same-page manual-cut caching should be refreshed after future model promotion. Spatial 20 km support derives from local leave-one-hole-out bins and does not establish new-district reliability (global leave-district-out skill: SWIR 0.062, TIR 0.007). No claim is made that these exploratory corrections validate geological truth.

Source ownership: geometry lane changed `clusters.py`, `fingerprints.py`, `rawcluster.py` and its correction tests; quality lane changed `data.py`, `features.py`, `robustness.py` and its correction tests; integration lane changed `analysis.py`, API grouping/validation metadata, method/cluster/map/similarity/manual-cut UI, integration tests and this audit. Existing unrelated changes remain uncommitted.

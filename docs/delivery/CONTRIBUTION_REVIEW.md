# Contribution decisions — 3 October 2026

Authorship does not establish correctness. These decisions use the inspected code and audit probes, followed by the shared-contract tests. They do not approve whole branches for merge.

| Contribution | Evidence | Decision for this delivery |
|---|---|---|
| Existing integrated system, `14039dd` | Source/model identities and useful API flow; gaps in depth validation, revision selection and centroid-based "similarity". | **Adapt.** Shared QC, active source revisions and query-distance ranking are implemented and tested. |
| Historical Sathi interval/quality helpers, `37fac20` | Useful source-row-aware interval aggregation; required clearer missing-mask eligibility and finite/physical-depth handling. | **Adapt.** Reuse structure, route admission through the shared contract, preserve source rows. |
| Jovan TIR work, already integrated through PR6 | Useful raw TIR extraction, interpretation and exploratory modelling. Deduplication based only on metadata can remove different spectra; finite spectral sentinels passed the original cleaner. | **Maintain and correct the inherited module.** Keep repeated readings and route its cleaner through the shared gate. The application and spectral pilot do not call his full pipeline. |
| Main-branch SWIR `s/u` analysis | Useful processing-level comparisons. Its experiment is not evidence that all future sources are equivalent. | **Reference.** Keep SWIR/TIR and processing levels explicit; use the reviewed interval runner for delivery. |
| Avantika TIR branch, audited `f25a698` | Scaling/seed diagnostics expose sensitivity; the saved 18/2 split and weak scaled/unscaled agreement are not geological validation. | **Reference.** No notebook code, trained model or assignments were imported. The current runner has its own diagnostics. |
| Yeshwanth raw PCA/distribution notebook on audited remote main `c57a024` | Interesting distribution comparison; hard-coded path, incomplete depth checks and absent Final Mask/spectral-sentinel filtering; saved population contains 15 of target20. | **Hold as a challenger.** No notebook code or output enters the application. Correct QC and reproduce a matched-cohort comparison before adoption. |

The implementation reused no unreviewed colleague model results. No competing method was added solely to increase feature count. An independent human review of the shared preparation remains an acceptance task; automated checks do not substitute for that review.

## What each person built, and what is actually used

Remote references were refreshed on 3 October 2026. This follow-up inspected source code, Git history and saved notebook outputs. It did not rerun either complete notebook. The isolated synthetic preparation check below is separate from their saved results.

### Yeshwanth: raw spectral distributions across whole holes

Source: [raw_data_PCA.ipynb at c57a024](https://github.com/Danielccn8/gswa-hylogger-data-crunch/blob/c57a02482f71e2ae62307454fbaab5d07cf18214/notebooks/raw_data_PCA.ipynb), following his initial addition at `a86a472`.

He loads paired TSG/BIP spectra for NIR (called SWIR in the notebook) and TIR separately. He preserves individual observations, records their original row, checks array lengths and wavelength agreement, and sorts spectra and metadata together. The latest version removes depth-bin aggregation.

His method fits PCA to retain 99% of variance, compares each hole's distribution along each retained component using one-dimensional Wasserstein distances, combines those distances, and clusters whole holes with average-linkage agglomerative clustering. It selects k using silhouette and visualizes results with PCA, distance heatmaps and MDS. The comparison retains variation within each hole, although it does not use the sequence of readings along depth or their joint distribution across components.

Saved output reports 15 holes and 1,027,111 observations per sensor, with four NIR and nine TIR components. Both selected k=2. These are saved exploratory results, not a fresh reproduction or evidence of superiority over the interval model.

**Actual adoption: none yet.** This is a useful candidate for whole-hole similarity. Before comparing it fairly, preparation needs Final Mask filtering, finite/non-negative depth checks and rejection of finite sentinel values. An isolated synthetic call to his preparation function retained all five supplied rows: valid, masked, spectral sentinel, negative depth and infinite depth. It already checks missing depth and finite spectra; those checks alone are insufficient. The experiment also needs portable paths, justified normalization, balanced training contributions and evaluation on held-out holes.

### Jovan: raw TIR extraction, PCA and clustering

Source: [TIR_PROCESSING.py at 59732a3](https://github.com/Danielccn8/gswa-hylogger-data-crunch/blob/59732a362d0e279ce68df2b2e4dbe581c925cb3a/src/hylogger/TIR_PROCESSING.py), previously merged through PR6. His earlier `b0a9f95` also contributed `docs/EAL_logs.md`.

He built download/availability helpers, TIR loading, cleaning with Final Mask, package-specific mineral-code decoding, wavelength alignment, SNV normalization, PCA retaining 95% variance and K-Means with candidate k values. His output describes clusters using TSG labels, plots spectra/composition/depth, and compares whole holes through the proportions of spectral clusters they contain. Its initial clustering unit is an individual spectrum.

**Actual adoption: inherited source retained and corrected.** The maintained [TIR module](../../src/hylogger/TIR_PROCESSING.py) keeps his work. Earlier delivery changes stopped deduplicating on metadata alone and rejected finite spectral sentinels; this delivery connected its cleaner to the common preparation contract. Regression tests cover different spectra with identical metadata and a sentinel spectrum.

The application's ETL does not import this module. The raw pilot uses `intervals.py` and `spectral_experiment.py`, with 1 m median spectra and transformations fitted on training data only. Shared use of SNV/PCA/K-Means should not be described as importing Jovan's full model or its results.

### Avantika: processed TIR baseline and sensitivity analysis

Source: [02_tir_ujclst_clustering.ipynb at f25a698](https://github.com/Danielccn8/gswa-hylogger-data-crunch/blob/f25a69814099afce0166905abc06d7c04ec41614/notebooks/30_clustering/02_tir_ujclst_clustering.ipynb) and its experiment configuration, following `fccd8ec`.

She uses the existing prepared-data/baseline helpers to represent each of the 20 holes by frequencies of processed `Grp1 ujCLST` labels. She standardizes varying features, compares K-Means k=2–5, inspects coverage and cluster profiles, tests ten random seeds and compares scaled with unscaled features. PCA is only a visualization in this notebook.

Saved output favors an 18/2 split, with `22USDH0207` and `SPBD0364` in the small group. Some seeds substitute `KEDD002` for `22USDH0207`. Scaled and unscaled assignments have ARI approximately -0.046, showing substantial sensitivity to preprocessing. Identifying that sensitivity is a valuable result, rather than a reason to dismiss the analysis.

**Actual adoption: methodological reference only.** No notebook code, model or cluster assignments were imported. The current dashboard computes profiles from reviewed interval products; the active processed model clusters intervals using square-root frequencies. Its own seed/representation diagnostics are not a reproduction of Avantika's whole-hole experiment.

## Credit boundary for the current application

The current processed route is `run_system.py -> system.py -> interval_model.py`; the raw pilot is `run_spectral_pilot.py -> intervals.py -> spectral_experiment.py`. Yeshwanth's and Avantika's experiments remain outside these routes. Jovan's maintained module remains available and tested, but is not their runtime dependency. Repository presence, methodological influence, direct code reuse and adoption of a trained result are different claims.

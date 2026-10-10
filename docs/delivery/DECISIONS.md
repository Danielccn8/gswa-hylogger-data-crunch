# Decision log

## 8 October — Explorer v2 decisions

| Decision | Reason and consequence |
|---|---|
| Separate worktree, compose project `hylogger_v2`, ports 8100/5434, data root `C:\HyLoggerData` | The earlier stack and OneDrive checkout stay untouched; short paths avoid Windows path-length failures; large data is not synced by OneDrive. |
| Download processed logs one log per request | The WA NVCL server returns HTTP 502 after ~2 min for multi-log `downloadscalars` requests (what nvcl-kit sends) but answers single logs in seconds. Columns are joined only after the StartDepth/EndDepth rows are verified identical. |
| Fetch raw spectra by HTTP byte range | THREDDS honours explicit ranges (not suffix ranges). Reading only spectral members transfers ≈13–15% of a package; members are CRC-checked and were byte-identical to a full download for LD82625W1. Full-ZIP mode remains available. |
| Raw summary mode is opt-in | Summaries (1 m medians, band features) are kept; source spectra are deleted only when the user ticks summary mode, so the statewide raw catalogue (1.9 TB of ZIPs) can be covered without filling the disk. |
| Mineral-level abundance from Min1–3 × Wt1–3 (Sathi) | Grp1 frequencies discarded most of the TSG information; the mineral level separates e.g. muscovite/phengite/paragonite. Narrow historical downloads without weights fall back to Min1 frequency and are flagged. |
| NOTAROK components are artefacts | Marker pen, core tray, wood, vegetation and tape are counted as `not_rock`, never as minerals; they previously surfaced as "rare minerals" in anomaly lists. |
| Hellinger K-Means with a Sathi K rule and a coarse Ward level | Readable clusters at two granularities; full K-Means with 10 starts gives seed ARI ≥ 0.93 where MiniBatch gave 0.3–0.7. |
| Staged analysis runs, explicit promotion | Retraining from the UI never silently changes what users see; old runs remain by ID. |
| The API enqueues jobs | A deliberate change from the read-only v1 API: requests only insert queue rows; the worker does all network and compute work. |
| Worker on the Windows host | SQLite's WAL download manifest fails across Docker bind mounts on Windows ("disk I/O error"). The container worker is kept as an optional profile for Linux/macOS. Versions are pinned in `platform/requirements.lock` for both. |
| Assays as an external reference | The GSWA package links 1,482 holes to WAMEX collars; mineral↔oxide correlations and per-cluster chemistry are stored with every run as consistency checks (the package marks these tables unverified). |
| Prediction reported with its limits | Leave-one-district-out shows almost no skill over the regional average; the app shows a support limit and labels distant estimates as regional priors. |
| Interim package stays out of Git and hosting | Its licence forbids sharing with third parties; the app shows a banner and runs locally. |
| Assay reporting codes are missing values | Some submissions use -5000 for "not analysed"; values ≤ -1000 are dropped, small negatives stay half the detection limit, and per-element plausibility caps (e.g. U, Th ≤ 10%, Au ≤ 1%) remove unit or column errors such as 19–99% U in 2 cm intervals. |
| Virtual assays only where skill is real | Chemistry is offered as a predicted track only when R² ≥ 0.3 on held-out holes (MgO, CaO, Na₂O, K₂O, LOI, Cr, Al₂O₃, Ni); U, S, Fe₂O₃ and SiO₂ are not predictable from HyLogger mineralogy. |
| No covariate regional model | Gradient boosting with location, tectonic region and IDW-from-other-districts features did not beat distance weighting on unseen districts; the app reports the test and keeps IDW with a 20 km support limit. |
| Adding holes refits the analysis | A statewide refit takes ~7–8 minutes and lets cluster definitions absorb new regions; `clusters.assign()` remains available for frozen assignment, but frozen anomaly and hole-embedding updates were not worth their complexity. |
| Automatic vs reviewed TSA from raw packages | The NVCL service publishes only the user (u) TSA run for WA; the system (s) run exists only in the raw `.bip` scalar plane. | Summarise both per metre during the raw summary and keep the small `.tsg` tables when sources are discarded; holes summarised earlier can be re-downloaded from the Hole page. | Comparison available for every hole summarised from now on; earlier discarded holes need one re-download. |
| Own TSG scalar mapping | pytsg maps class codes only for class tables numbered above 0 and raises KeyError for codes absent from a table (PLD010). | `raw_summary.read_section` maps every class table and labels unknown codes “class code N”; identical to pytsg elsewhere (checked on 05GJD001). | System TSA minerals named correctly; packages with stray codes no longer fail. |
| Gentler raw downloads | THREDDS resets connections in bursts (TLS resets for minutes at a time). | Back off up to 2 min per range request, retry the size probe, 4 parallel parts per member instead of 8, and 1 h per hole for spectra-only fetches. | Failed holes are retried in a later pass instead of stalling the queue for 4 h. |
| Explanations computed from stored products | Users could not tell why holes are similar or metres unusual. | Reasons and evidence are derived at request time from the stored fingerprints, labels, profiles and assays; no model is re-run in a request. | Every similarity list and anomaly flag carries plain-language evidence with numbers. |
| Sequence similarity by banded DTW on 10 m TVD steps | Mix-based fingerprints ignore order; DTW compares profiles (e.g. regolith → alteration → fresh rock) while tolerating thickness changes. A Sakoe-Chiba band (30% of relative position) prevents degenerate warps; distances are normalised by summed length so long and short holes compare. Very deep holes are coarsened to at most 120 steps. |
| DASC mineral-log CSVs not scripted | The package's MINERALLOG links return HTTP 403 to programmatic requests (Azure application gateway). They are not circumvented; raw summaries cover 444 of the 563 holes without public processed logs. |

## 3 October delivery decisions

| Decision | Reason and consequence |
|---|---|
| Adopt Analytical overview and retain a separate map | User selected this direction; the default screen explains patterns and support. Four views share dataset, sensor and visible holes. |
| Use the shared `reading-contract-v2` gate | Raw and processed meanings differ; share identity/QC, not a forced feature representation. Raw primary analysis rejects absent masks, processed analysis labels the limitation. |
| Snapshot API responses and protect broader selected logs | Avoid losing source history when a narrow profile is fetched. No positional joins across responses. |
| Select exactly one active interval set | Prevent counts/profile/export from pooling revisions. Keep historical products accessible by ID. |
| Freeze 14 development / 6 regional-holdout holes | Avoid random neighbouring-row leakage; test transfer to a whole regional group. This remains an engineering split. |
| Balance K-Means training by hole | Long holes should not automatically dominate fitted centres. Silhouette remains interval-weighted and is labelled accordingly. |
| Preserve frozen models at the 50-hole gate | Report incompatible TIR features (15 rows) without refitting on the expanded cohort. |
| Keep the raw spectral result as a separate pilot | Large NIR training-distance exceedance indicates limited coverage. More raw data and preprocessing evaluation are required before promotion. |

## Earlier decisions (historical)

| Issue | Options and evidence | Choice | Consequence |
|---|---|---|---|
| Base and isolation | Local `main` is clean but three commits behind; `origin/main` contains Jovan TIR and SWIR scripts. | Worktree from `origin/main` `1cd4ffc`. | Existing team work retained; no remote mutation. |
| Processed mask | NVCL processed Parquet contains no `Final Mask`; raw TSG may have it. | Report `unavailable_in_processed_log`; raw path requires `Final Mask==1` when present. | Processed comparisons cannot claim masked-rock acceptance. |
| Repeated depth | Two different readings can share depth; metadata-only dedup loses information. | Keep source rows; bin by depth but retain every row identifier. | Frequency may reflect scanning density; review with support-weighting later. |
| TSG SWIR/TIR | Their groups represent different methods and spectral regions. | Independent interval sets and models. | Cluster numbers are not comparable across regions. |
| Geometry | Team SWIR used direct proportions; Avantika TIR scaling sensitivity was high. | Square-root frequencies and development-only k search; compare direct frequencies and PCA on the same population. | Internal separation changes with representation; no geological winner is claimed. |
| Data publication | Original data and licence status are not verified for redistribution. | Read originals, write generated artifacts to ignored worktree data. | Demo acquisition instructions replace bundled real data. |
| Reuse | Sathi's earlier commit contains row-alignment, QC and interval logic; Jovan's TIR and SWIR scripts remain integrated. | Adapt the modules and preserve existing scripts/notebooks. | Historic scripts remain available; new runner calls reusable modules. |
| Sentinel | `-3.402823e+38` and nonfinite spectra are invalid. | Filter paired metadata/spectra rows together and report exclusion. | No hidden index shift or accidental numerical cluster. |
| Density comparison | HDBSCAN can leave substantial noise. | Bounded 80-interval-per-development-hole comparison, no deployed assignments. | Noise and coverage are reported beside non-noise silhouette. |
| Spectral source | Extracted TSG packages contain adjusted spectra, not untouched instrument files. | Separate two-hole SNV/PCA/K-Means engineering pilot with Final Mask. | It is neither independent truth nor population generalisation. |
| Model reuse | Installed scikit-learn version differed from an older model binary. | Include dependency versions in new model identity/card and reject mismatched inference. | Frozen inference must use matching versions; re-fit gets another model ID. |
| Catalogue reconciliation | A partial snapshot could omit valid old holes. | Make `load_holes.py` preserve by default; deletion requires explicit `--prune`. | Incremental local loading cannot silently erase historical records. |
| Local network | Compose demo credentials are local examples. | Bind API and database ports to `127.0.0.1`. | Default local Compose does not expose the services to the LAN. |
| Whole-hole comparison | The active model groups depth intervals, while the team wants holes compared as units. Reading count can reflect scan density. | Average diagnostic 1 m interval compositions equally for each hole; fit separate SWIR/TIR exploratory K-Means on the fixed 20-hole batch. | Whole-hole labels are separate from interval labels; no geological class is inferred. |
| Anomaly screen | No independent assays or lithology validate anomalies. A small cohort always has farthest members. | Show the top 10% by nearest-neighbour distance as relative review candidates with QC and source IDs. | The flag directs inspection and cannot be called a geological anomaly, prospectivity result, or probability. |

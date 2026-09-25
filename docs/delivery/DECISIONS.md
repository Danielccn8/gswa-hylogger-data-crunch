# Decision log

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

# HyLogger next delivery plan

Planning baseline: 3 October 2026. This document plans the next implementation; it does not mark that work complete. Read [System explained](../SYSTEM_EXPLAINED.md) for the compact explanation of the current system.

**Implementation update, 3 October:** Analytical overview was selected. Shared QC, source snapshots, active interval revisions, explicit regional holdout and the four connected workspaces are implemented locally. Processed target20 and the 50-hole checkpoint have run. Raw coverage remains two of the target20; broader raw acquisition, the 200-hole checkpoint and external acceptance remain open. The numbered plan below preserves the approved baseline; see [current evidence](EVIDENCE_2026-10-03.md) for exact results.

## 1. The delivery we are building

Help a geoscientist understand the acquired dataset, compare drill holes, inspect unusual intervals and export the evidence. Open with a clear statistical overview. Keep the map as a separate geographic workspace connected to the same selections.

Start with the user's selected 20 raw-package holes for detailed spectral work, recovering missing downloads as needed. Use processed TSG logs through NVCL for broader coverage, expanding through measured batches. Twenty is a starting cohort, not a permanent system limit. More data must not require redesigning the application.

Deliver three things together: a usable application, a reproducible analytical pipeline and a short explanation of how it works. Geological acceptance, engineering acceptance and publication remain separate outcomes.

## 2. Evidence and starting boundaries

| Item | Verified starting position | Consequence |
|---|---|---|
| Shared code | Remote `main` is `c57a024`; the primary local checkout is `1cd4ffc`. | Review the changes before integration. |
| Local system | `codex/hylogger-system` at `14039dd` contains the integrated ETL, models, API and viewer. | Preserve it as the reference candidate; it also requires review. |
| Tests | The audit passed 43 tests in the primary checkout and 50 in the system worktree. Synthetic probes exposed defects outside that coverage. | Passing existing tests is necessary but insufficient. |
| Saved system products | 26 processed holes, 50 sensor products and 11,475 interval rows were inspected. Docker was unavailable during the audit. | Saved evidence is not a fresh running-system check. |
| Raw inventory | The user clarified that the intended 20 may have been cleaned up. Five extracted holes are present in the primary checkout; only LD82625W1 and WKDD15001 belong to the intended 20. Both have ZIPs and two spectral file pairs. | Reuse and verify those two, then refresh availability/size estimates for the other 18. Structure alone is not a numerical QC pass. |
| Cohorts | The saved system comparison and `team_raw_tsg_20` share only two holes. The newer spectral notebook has saved outputs for 15 holes. | Give each cohort an explicit identity; do not compare their scores as equivalent experiments. |
| Validation references | No assay/lithology database or completed geological acceptance was found in the reviewed folders. | Continue engineering; limit scientific claims until independent review/data exists. |

Only planning documents and a conversation design preview are created in this planning step. No application changes, model training, bulk downloads, migrations, merges or publication are part of it.

## 3. Reuse only after review

Apply the same standard to teammate contributions and the existing system. Neither authorship nor integration into `main` establishes correctness.

For each candidate, record its commit and purpose, inspect the actual code, reproduce a small case, compare it with the reference pipeline and decide **reuse**, **adapt**, **reference only** or **exclude**. Record the reason and any attribution. Do not merge a whole branch to obtain one useful function.

Acceptance requires correct source alignment, compatible QC and units, no train/test leakage, reproducible inputs/configuration, justified resource use and a demonstrated contribution to the agreed task. A competing method needs a fair comparison on the same cohort and analysis unit. A method that adds complexity without useful evidence stays out of the delivery.

Candidates: SWIR `s/u` analysis; TIR parsing and decoding; Avantika's sensitivity diagnostics; Yeshwanth's spectral-distribution comparison; historical Sathi quality/holdout helpers. None is approved for adoption by this document.

## 4. One preparation contract, two source adapters

In this repository, `raw` normally means a downloaded package containing TSG headers and adjusted spectra. It must not automatically be described as uncorrected instrumental raw data. Processed API logs are TSG interpretations. The two sources share provenance and quality rules, but do not contain equivalent measurements.

| Shared contract | Spectral-package adapter | Processed-TSG adapter |
|---|---|---|
| Identity | Hole, package revision, sensor, checksum and original row | Hole, provider dataset/revision, source response and original row |
| Measurements | Reflectance/emissivity values and wavelength grid, with units verified | Decoded groups/minerals, fit weights/errors and their processing level |
| Quality | Paired metadata/spectrum filtering; mask policy; finite values; sentinel and band checks | Missing/sentinel decoding; depth checks; log availability; masks only when actually supplied |
| Derived row | Initially a 1 m interval spectrum plus support and source rows | Initially a 1 m distribution of diagnostic primary group labels plus QC |
| Model input | Region-specific spectral representation | Region-specific TSG group-frequency representation |

### Rules that must pass before model selection

1. Preserve original files. Store derived artifacts separately and version the source, QC policy, feature schema, interval width and code/configuration used.
2. Assign `source_row` before filtering. Every mask, selection and depth sort must move metadata and spectra together. Test the association, not only equal row counts.
3. Reject non-finite, sentinel and invalid physical depths explicitly. Validate units before conversion. Retain repeated depths unless an independently justified duplicate policy proves the whole measurement is redundant; identical metadata is not enough.
4. Distinguish masked/problematic measurements, unknown masks, invalid spectra and valid but nondiagnostic interpretations. `Aspectral` is not a mineral and is not automatically non-rock. Valid spectral readings can remain useful without a diagnostic TSG label.
5. For the primary spectral analysis, use Final Mask-admitted rows when a verified mask exists. A missing mask must produce a separate, clearly labelled eligibility state; do not silently call that data clean core. Define any sensitivity run separately.
6. Confirm class dictionaries and `s/u/d` processing levels. Unknown codes remain unknown. Never interpret TSG fit weights as calibrated confidence or physical mass percentages.
7. Verify wavelength count, order, finiteness, units and common range. The `nir` package section can span VNIR and SWIR: record the actual band range. Do not interpolate outside observed support or merge SWIR/TIR into one physical composition.
8. Keep missing logs distinct from zero observations of a category. Fill category zeros only within a confirmed populated log. Reject or explicitly resolve multiple revisions.
9. Reconcile received/admitted/excluded/unknown counts using mutually exclusive primary reasons. Keep secondary flags separate. Report the extra loss from sparse intervals. Observed source depth span and reading count are not recovered-core metres.
10. Join raw and processed sources only with verified dataset/revision correspondence. Never match independently sorted rows by position. Assays and lithology require identifier validation and interval-overlap joins.

Use 1 m as the initial comparison unit, then assess 0.5/1/2 m on development data. Keep the original readings, so later work can inspect narrow features or compare full spectral distributions. Aggregation is a derived view, not data deletion.

### Proof required

Run focused regression cases for the defects reproduced in the audit, including finite sentinels, infinite/negative depths, repeated-depth distinct spectra, missing masks and incompatible grids. Verify at least two real holes against original row IDs and masks in both sensors. Re-run identical inputs and confirm equivalent products without duplicate records. A second reviewer checks the source-to-output trace before approving the shared preparation.

## 5. Data acquisition and expansion

The user confirmed this target cohort. It matches `configs/selections/team_raw_tsg_20.yaml`:

| Proposed regional group | Holes |
|---|---|
| Kalgoorlie (7) | KEDD002, LD82625W1, WKDD15001, FDH002, SPBD0364, FDH007, NBDH035 |
| Leonora / northern Goldfields (7) | MWEX10270, 22USDH0207, 15RCDVZ001, 16CADD002, 13TRDD012, 13TRDD013, RRLGDDD093 |
| Eastern Pilbara / Paterson (6) | PND003, PND005, 16MYD0047, EPT2276, 16MYD0052, PND004 |

These are broad study groupings, not verified homogeneous geological classes. The existing cohort report records related projects, missing trays in WKDD15001 and depth/length discrepancies for some holes. Recheck those exceptions. Split by project as well as hole where feasible, so neighbouring holes do not create misleading confirmation results.

Create one inventory with hole IDs, exact paths, archive/extraction status, complete `.tsg`/`.bip` pairs, revisions, sizes, hashes and QC state. A directory or a named selection does not prove a completed download. Register existing sources without copying or re-downloading them unnecessarily.

Acquisition order: (1) verify LD82625W1 and WKDD15001 and use 05GJD001 outside the target cohort as a regression fixture; (2) fetch/reuse processed logs for all 20; (3) refresh remote availability and disk estimates; (4) acquire a small cross-region raw batch, verify it, then complete the remaining target set. Missing packages may be downloaded under the user's instruction; do not launch a bulk transfer as part of planning. Keep replacement decisions visible if a source is unavailable.

The 11 September cohort report estimated 46.93 GB for all 20 ZIPs before extraction. This is historical, not a current quotation or the remaining download size. The downloader retrieves full ZIPs even with `--components spectra`; verify free space and current sizes, reuse complete files, and preserve resume state.

Use separate named cohorts: **spectral development**, **spectral confirmation**, **processed coverage** and their **matched comparison subset**. Freeze the manifest and split before tuning. Already explored holes are development evidence; seek genuinely unexamined holes for confirmation. Choose splits by whole hole and, where feasible, region/instrument context, not neighbouring rows.

For processed logs, probe a small representative set first. Measure request time, bytes, log availability, failure rate and retry behaviour. Then process 20 -> 50 -> 200 compatible holes, expanding further when resource and quality gates pass. These are checkpoints, not capacity promises. Report coverage over all requested holes, including failures and missing sensors.

API logs are expected to be lighter than spectral archives, but rate limits, availability and schema differences still require measurement. Preserve broad downloaded tables: the existing downloader can replace them when called with a narrower profile. Before scaling, implement source snapshots or another non-destructive merge/version policy, bounded retries, resumable manifests and an explicit dataset-selection rule.

## 6. Analytical choices

| Question | Initial method | Evidence required |
|---|---|---|
| What is in the acquired cohort? | Descriptive statistics and original TSG profiles | Explicit denominators, missingness and sample coverage |
| Which intervals have similar interpreted patterns? | Separate SWIR/TIR K-Means on square-root group frequencies; compare a simple dominant-group baseline | Whole-hole validation, cluster profiles, coverage and sensitivity |
| Do spectra show a related pattern? | QC spectra -> justified region-specific normalization -> training-only PCA -> K-Means | Matched cohort, frozen transformations, source spectra and independent checks |
| Which holes resemble one another? | Profiles derived from eligible intervals; benchmark against original-feature similarity | State weighting, distance, support and treatment of missing sections |
| What should a geologist inspect? | Unusualness rankings plus QC and contextual evidence | Stable ranking, ordinary controls and documented geological review |

PCA reduces dimensions; it does not create clusters. Do not force a fixed `k`, SNV, scaling or another normalization on every source. Select candidates on development data and report the reasons. Balance training by hole or use justified sampling/weights so the longest holes do not dominate automatically. Do not compare silhouette values from different geometries as if they measure the same target.

Check sensitivity to seeds, interval width, hole/block resampling, processing level, missing coverage and instrument/region. Fit vocabulary, scaling, PCA, clusters and outlier thresholds on development/training only. Keep the confirmation set untouched until choices are frozen. Report uncertainty and exclusions; many neighbouring readings are not independent geological samples.

Yeshwanth's distribution method and HDBSCAN are optional challengers, not mandatory features. Their inclusion depends on corrected QC, fair evaluation and usefulness. New compatible holes use frozen inference; retraining creates a new version. Explicitly handle out-of-schema or out-of-distribution inputs.

## 7. Product and visual direction

Prefer the existing Python, FastAPI, PostGIS and Leaflet stack, with a modular frontend. Do not migrate frameworks just to improve appearance. Split the current large page into manageable views/assets where helpful and use consistent chart components. The API reads precomputed, versioned results.

| Workspace | User question | Initial content |
|---|---|---|
| **Overview** (default) | What is available, and what patterns deserve attention? | Up to four factual summary values; original-unit group profiles; sample/support distribution; compact drill-hole table |
| **Compare** | How do selected holes or groups differ? | Two to four profiles on shared scales; SWIR/TIR shown distinctly; differences, support and similarity definition |
| **Map** | Where are those holes and patterns? | Existing geographic view, selection and region filters, linked to the same profiles |
| **Data & method** | Can I trust and reproduce this result? | QC reasons, availability, model summary, sources, versions and processing failures |

A shared hole-detail view contains downward depth tracks, selected interval evidence, source spectrum when available and export. It is reachable from charts, tables and the map. Keep cohort, sensor, filters and selected model consistent across views; geographical filtering changes the displayed population, not the trained model.

The overview should lead with patterns and their support, not model jargon. Every chart answers one question, names its unit/denominator, shows the filtered population and offers a useful next action. A model-quality score is not a prospectivity score. Label unusual cases as review candidates. Show unavailable sensors and empty selections explicitly.

Visual direction: quiet neutral surfaces, generous spacing, strong text hierarchy, restrained teal accent, tabular numbers, consistent labels and few simultaneous colours. Avoid decorative gradients, excessive cards, 3D charts and dense dashboards. Use two main plots and one table initially; put detailed methodology behind disclosure. Maintain accessible contrast, keyboard operation and labels beyond colour.

Compare two overview layouts before implementation: **Analytical overview**, with plots first, and **Review workspace**, with a sortable evidence table first. Prefer Analytical overview for a first-time GSWA user; retain the review workflow in Compare/detail. The conversation preview illustrates layout using the saved processed cohort, not a new analysis of the reported 20 raw holes.

### Product correctness gates

- Replace the current centroid-ranked similar-interval list with actual distance to the selected interval, or label it accurately as representative cluster members until that work is done.
- Select an explicit active interval set/source revision per result. Profiles, counts and exports must not accumulate historical revisions or incompatible widths.
- Keep processed models, spectral models and whole-hole comparisons distinct in labels and version identity.
- Do not load the full spectral archive for a dashboard request. Precompute summaries and read bounded detail artifacts.
- Confirm that visible filters, chart counts, selected rows and exported CSV refer to the same population and version.

## 8. Implementation sequence and gates

| Stage | Work | Exit evidence |
|---|---|---|
| **A. Freeze the starting evidence** | Inventory target raw20, plan missing acquisitions; inventory processed sources; preserve current candidate and artifacts; record contribution decisions. | Reconciled manifest, named cohorts and review matrix. |
| **B. Prove preparation** | Shared contract/adapters, regression fixes, source versioning, real-data QC. | Synthetic edge cases, two-hole paired trace, reconciled QC and repeatability. |
| **C. Reproduce and select models** | Re-run corrected baselines, fair comparisons, whole-hole/region validation and case review pack. | Versioned model cards, defensible choice, uncertainty and limitations. |
| **D. Integrate the product** | Overview/Compare/Map/Method, coherent API and active versions, useful similarity and exports. | Browser walkthrough and consistent source-to-chart-to-CSV evidence. |
| **E. Expand and deliver** | Processed 50/200 gates, new-hole inference, failure recovery, clean-machine rehearsal and handover. | Time/RAM/disk/network measurements, reproducible demo and documented remaining limits. |

Design can progress beside B using a frozen example contract; it must not present provisional data as accepted science. Expansion starts after B and runs beside C/D without blocking the first useful release. Do not increase scope to compensate for missing validation.

At each implementation stage, update [System explained](../SYSTEM_EXPLAINED.md) in the same change: what changed, how it works, why and how it was checked. Update `EXECUTION_STATUS.md` for detailed progress and `DECISIONS.md` for tradeoffs. The compact guide remains the user's entry point, not a second technical log.

Before committing to a calendar, confirm the actual final submission date and available team capacity. If the 12 October demo remains the target: aim for A/B on 3-5 October, C/D on 6-8 October, then E and rehearsal on 9-11 October. These are planning targets, conditional on data and gate results, not an assurance that all expansion targets fit.

## 9. Delivery acceptance and later expansion

A teammate can install the system, open the overview, understand coverage, compare holes, inspect a depth interval, open the same evidence on the map and export it with source/model versions. A fresh run reproduces the result within documented tolerances. A failed source remains visibly failed. Another reader can explain the preparation and clustering using the compact guide.

Suggested performance targets, to be measured on a named machine and cohort: precomputed overview within 3 seconds and a typical hole profile within 1 second. Test the fallback demo without network; the current map's external scripts/tiles are a known dependency, so cache/package permitted assets or provide a useful table/profile fallback.

Later increments can add wider processed coverage, more raw holes, richer spectral representations, assays/lithology and a validated file-import workflow through the same contracts. Retain extension points through manifests, schemas, model versions and source adapters; do not build a plugin framework now. Keep each release's demonstrated coverage explicit.

Still to resolve: current availability and acquisition cost of the missing raw packages; the precise scientific question within the selected regions; external validation availability; delivery/access environment; and whether self-service upload belongs in this release. These questions do not block the planning or the independent preparation fixes.

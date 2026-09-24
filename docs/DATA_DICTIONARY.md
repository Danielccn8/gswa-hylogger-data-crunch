# Data dictionary

| Field | Unit / meaning | Unknown / provenance |
|---|---|---|
| `hole_id` | NVCL drill-hole identifier | Original provider ID; conflicts require review |
| `dataset_id` | Provider dataset revision | Parent of the input Parquet |
| `source_sha256` | File checksum | Same exact input bytes |
| `source_path` | Relative path below acquired-data root | Never use alone as an immutable revision; combine with checksum and dataset ID |
| `source_rows` | Zero-based Parquet row positions | Links interval to acquired scalar log; not a TSG package row without alignment proof |
| `batch_id` | One local ETL/inference request | `batch_run.summary` stores requested, completed and failed products plus resources |
| `interval_set_id` | Identifies a source checksum, hole, region and bin width | Aggregation-code version is not yet part of this key; changing semantics needs a migration |
| `interval_id` | Identifies one bin inside one set | Stable on identical source/parameters |
| `depth_from_m`, `depth_to_m` | Metres down hole, fixed bin edges | No claim of continuous core support |
| `source_depth_min_m`, `source_depth_max_m` | Metres, observed row range | Min/max do not measure full coverage |
| `n_total` | Rows with valid depth in bin | Includes excluded or nondiagnostic rows |
| `n_admitted` | Diagnostic rows admitted to features | Denominator for frequencies |
| `features` | Frequency, 0–1, of primary TSG group among admitted rows | Absent group differs from an absent interpretation; not rock mass fraction |
| `primary_reasons` | Mutually exclusive row states | Sum equals `rows_received`; secondary flags must not be added |
| `reason_counts` | Mutually exclusive counts within a valid-depth bin | Sum equals `n_total`; invalid-depth rows are in the product QC, outside bins |
| `mask_status` | Available or unavailable | Processed API logs here lack Final Mask; no assumption that missing mask means rock |
| `cluster_id` | Arbitrary integer within model | No equivalence across SWIR/TIR or model versions |
| `distance` | Euclidean distance to K-Means centroid after square-root transform | Not probability, confidence or mineralisation score |
| `model_id` | Frozen run identifier | Refit creates a different model; model card records schema and split |
| `outside_training_distance` | Adjusted-spectral pilot flag | Distance exceeds a development-only 99th-percentile threshold; not mineral prospectivity |

`INVALID`, `Aspectral`, `NotARok` and missing labels are not mineral groups. A non-diagnostic interpretation does not prove that the spectrum itself was invalid. TSG weights are fit proportions, not rock mass percentages or calibrated certainty.

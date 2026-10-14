Evidence inventory:
- stage_refresh.py/log: reproducible full local refresh, no promotion
- before_active.json: prior15active run rows/cards/product paths
- source_frozen.json: mainstage source snapshot; robustness-only authorized follow-up causes broad source_unchanged=false
- staged_runs.json/stage_result.json: committed candidate results (written on success)
- run_robustness.py/robustness.log: exactcandidate linked bounded development-only validation;24widthholes per sensor
- validation_source.json + width_sources_before/after.json: validation source and before/after pilot parquet hashes linked to active feature QC
- environment.json: local computational package versions and thread settings
- checks.json: verification statuses, later updated after fullstage and finalchecks
- final_summary.json: cohort/gates/distance/card review evidence
- raw_sources_post.json: post-run raw input checksums with explicit capturetime, cannot prove pre-run stability
- served_before_method.json and served_after*.json: runtime boundaries and method/provenance smoke
Local coherent activation requires parent review. Existing dirty work, original run artifacts and activeIDs are preserved.

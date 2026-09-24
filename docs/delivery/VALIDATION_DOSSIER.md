# Validation dossier for geological review

Status: prepared for review, **no independent geological acceptance**. The
processed uTSAS/ujCLST interpretations and adjusted TSG spectra are related
outputs. Their agreement is not an assay or independent lithology observation.

## Reproducible cases

| Case | Location | Question for reviewer | Current engineering observation |
|---|---|---|---|
| Ordinary profile | 05GJD001 SWIR 150–151 m | Do the source labels, mask limitation and interval support make sense? | Product `60df8d254d8440036ce3`; report reconciles all 25,938 source rows; processed Final Mask unavailable. |
| Spectral contrast | LD82625W1 SWIR 148–149 m | Is the adjusted spectrum plausible given TSG interpretation and instrument context? | Spectral pilot reports `outside_training_distance`; do not treat as mineral prospectivity. |
| Sensor missing | 07THD003 TIR | Is TIR absent from this dataset revision or available via another provider revision? | Reviewed Parquet lacks `grp1_uj_clst`; no TIR cluster is shown. |
| Width sensitivity | 05GJD001, both regions | Are group boundaries stable at 0.5, 1 and 2 m? | SWIR bin counts 333, 167, 84; admitted reading count stays 14,252. TIR counts 333, 167, 84; admitted 18,441. See `data/system/sensitivity_2_holes.json`. |
| Density alternative | 26-hole development sample | Would large HDBSCAN noise be acceptable for exploration? | At min size 20, SWIR noise 71.41%, TIR noise 79.84%; no deployed HDBSCAN labels. |

## Checks required before geological claims

1. Obtain assay/lithology records with source IDs, depth units, interval
   boundaries, coordinate context and revision. Check that each hole ID maps to
   the same physical hole; unresolved identity remains excluded.
2. Convert units explicitly. Compare depth intervals by geometric overlap,
   never by row position, and retain zero, missing and below-detection values
   as distinct states.
3. Select a genuinely new confirmation cohort before reviewing it. Record
   instrument, tray, scanner settings, wavelength grid and `s/u` processing
   state. Apply frozen models and report rejection/out-of-schema rates.
4. Ask a domain reviewer to inspect ordinary, representative, discordant and
   outlying intervals with neighboring depths, QC and original spectra. Record
   reviewer identity, date, evidence and disagreements. No such review was
   performed here.

Allowed current claim: a reproducible local grouping and exploration tool exists
for the compatible acquired cohort. Unsupported claims: mineral abundance,
prospectivity, geological class accuracy, state-wide model validity and GSWA
acceptance. `outside_training_distance` is a model-distance flag, not a
confirmed geological anomaly.

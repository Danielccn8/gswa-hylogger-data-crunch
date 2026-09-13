# Shared raw + TSG cohort: 20 holes

Verified 11 September 2026. This replaces the earlier provisional roster. Retains the five NCI-available teammate holes and all eight accepted additions; replaces the seven absent NCI packages. Region counts: 7 Kalgoorlie, 7 Leonora/northern Goldfields, 6 eastern Pilbara/Paterson.

## Verification boundary

All 20 are in the fresh NCI catalogue. Actual HTTP range requests succeeded and ZIP central directories were parsed. Every ZIP contains positive-size NIR/SWIR `.tsg`/`.bip` and TIR `_tir.tsg`/`_tir.bip` pairs. Every archive dataset directory ID matches its NVCL dataset ID. SWIR uTSAS and TIR ujCLST Min1 and Grp1 scalar contents were downloaded, and more than 50% of rows in each primary-group log have valid mineral-group labels. All NVCL metadata identify HyLogger 3-2.

**These checks establish acquisition availability and package structure.** Full ZIP download/CRC integrity, actual wavelength grids, numerical spectra, Final Mask, sample alignment and physical core continuity still require ETL QC. Matching dataset IDs alone does not prove identical revisions or row order. These are spectral files distributed with processed TSG products, not a guarantee of uncorrected instrument Level 0 data.

## Roster

| Hole | Team group | Origin | Project | Reported core m | SWIR valid group % | TIR valid group % |
|---|---|---|---|---:|---:|---:|
| KEDD002 | Kalgoorlie | retained teammate | Kanowna East | 969.80 | 59.5 | 70.5 |
| LD82625W1 | Kalgoorlie | retained teammate | St Ives | 676.40 | 70.0 | 73.3 |
| WKDD15001 | Kalgoorlie | retained teammate | West Kundana EM Anomaly | 317.80 | 73.2 | 73.6 |
| FDH002 | Kalgoorlie | previous addition | Feysville | 283.60 | 69.8 | 75.8 |
| SPBD0364 | Kalgoorlie | replacement | Polar Bear | 408.18 | 77.5 | 97.3 |
| FDH007 | Kalgoorlie | replacement | Feysville | 223.60 | 70.6 | 71.0 |
| NBDH035 | Kalgoorlie | replacement | Nimbus | 537.60 | 68.0 | 72.6 |
| MWEX10270 | Leonora / northern Goldfields | retained teammate | Mt Weld fresh carbonatite | 1020.40 | 78.0 | 80.6 |
| 22USDH0207 | Leonora / northern Goldfields | retained teammate | Ulysses | 576.68 | 66.6 | 99.3 |
| 15RCDVZ001 | Leonora / northern Goldfields | previous addition | Valdez prospect | 403.60 | 62.3 | 75.2 |
| 16CADD002 | Leonora / northern Goldfields | previous addition | Callisto | 882.30 | 68.8 | 77.9 |
| 13TRDD012 | Leonora / northern Goldfields | replacement | Jaguar | 768.00 | 74.5 | 75.7 |
| 13TRDD013 | Leonora / northern Goldfields | replacement | Jaguar | 315.00 | 75.4 | 78.1 |
| RRLGDDD093 | Leonora / northern Goldfields | replacement | Garden Well | 412.20 | 52.7 | 73.3 |
| PND003 | Eastern Pilbara / Paterson | previous addition | Paterson North | 200.60 | 59.5 | 74.3 |
| PND005 | Eastern Pilbara / Paterson | previous addition | Paterson North | 415.00 | 75.1 | 79.6 |
| 16MYD0047 | Eastern Pilbara / Paterson | previous addition | Telfer | 546.70 | 60.9 | 75.2 |
| EPT2276 | Eastern Pilbara / Paterson | previous addition | Yeneena, Aria prospect | 385.30 | 62.5 | 72.7 |
| 16MYD0052 | Eastern Pilbara / Paterson | previous addition | Telfer  | 381.60 | 62.6 | 76.8 |
| PND004 | Eastern Pilbara / Paterson | replacement | Paterson North | 212.90 | 66.7 | 71.2 |

## Seven replacements

- SPBD0365 â†’ SPBD0364: nearby Polar Bear comparison.
- FRCD002 â†’ FDH007: Feysville, with a longer logged interval.
- LEFD001 â†’ NBDH035: Nimbus, within the broad Kalgoorlie group; this changes project and commodity.
- EMSD1388 â†’ 13TRDD012: Jaguar, northern Goldfields.
- MCPDH0002 â†’ 13TRDD013: Jaguar, northern Goldfields.
- 16CADD001 â†’ RRLGDDD093: Garden Well, northern Goldfields. The already accepted 16CADD002 retains a Callisto-area example.
- PND002 â†’ PND004: same Paterson North project.

These are roster substitutions, not claims of equivalent lithology. The seven excluded packages are absent from the current NCI catalogue and their previous links returned 404. Do not infer that spectra do not exist elsewhere.

## Alternative source route

GSWA documents TSG access through GeoVIEW.WA and the AuScope Portal. The GeoVIEW guide describes preparing TSG files from the database for download, requiring an email address and offering optional linescan imagery. These may be access routes into the same NVCL infrastructure, not independent mirrors. No request was submitted, and availability of any excluded hole through this route was not established. NCI is the consistent acquisition source for this cohort.

## Important study qualifications

- Region names are broad team groupings. The eastern group is Paterson/Yeneena context, not a homogeneous Pilbara Craton sample; Kalgoorlie and Leonora are both within the broader Eastern Goldfields context. The manifest preserves tectonic location and coordinates.
- Complete here means both spectral products and the required interpretation logs for the available scanned interval. It does not mean every metre from surface to end-of-hole. WKDD15001 explicitly lacks trays 1â€“17. It is retained as requested, with this exception.
- Reported core length, logged depth extent and usable spectral metres differ. Preserve gaps. Do not rank holes only by maximum depth.
- 13TRDD012 has 768 m reported in the current GSWA list and scalar depths approximately 91.25–859 m, while the older local catalogue recorded 574 m. RRLGDDD093 has a 465.9 m depth extent but 412.2 m reported core. Both distinctions are preserved in the manifest.
- Some additions are neighbouring holes in the same project. This is useful for the first clustering comparison, but treat project and hole as groups when assessing generalisation.
- Keep dates as context. Catalogue dates and NVCL drilling dates can disagree; dataset or ZIP modification dates are not scan dates. Prioritise scanner and processing consistency over recency.
- Valid group percentages exclude null, INVALID and NOTAROK labels. They are not confidence, raw spectral quality or mineral abundance. Fit raw clustering without mineral labels; use labels for comparison afterward.
- Use balanced per-hole sampling, fixed seeds and common preprocessing to avoid deep/densely scanned holes dominating. Fit SWIR and TIR separately first; align source samples before joint analysis.

## Reproducible use

The exact sum of ZIP sizes is **46.93 GB** (decimal). The current downloader fetches full ZIPs even when extracting only spectra, so allow space for ZIPs plus extraction. Acquisition has not been run in full.

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\hylogger.exe selection show team_raw_tsg_20
.\.venv\Scripts\hylogger.exe raw fetch --selection team_raw_tsg_20 --components spectra --dry-run
.\.venv\Scripts\hylogger.exe raw fetch --selection team_raw_tsg_20 --components spectra
.\.venv\Scripts\hylogger.exe processed fetch --selection team_raw_tsg_20 --profile minerals
```

The current minerals profile includes both `*TSA*` and `*jCLST`. Review downloaded dataset IDs against the manifest before pooling; do not assume a later live download is unchanged. No clustering or ETL code was modified for this selection.

## Files and sources

- Selection: `configs/selections/team_raw_tsg_20.yaml`.
- Manifest: `reports/cohorts/team_raw_tsg_20.csv` (CSV is locally ignored under the existing repository rules; share explicitly with the team).
- Audit evidence: `reports/cohorts/team_raw_tsg_20_evidence.json`; contains actual ZIP member names, declared sizes/CRCs, dataset IDs and inventory metadata. Declared member CRCs are not full integrity checks.
- [GSWA HyLogger access](https://www.wa.gov.au/organisation/department-of-mines-petroleum-and-exploration/geological-survey-of-western-australia/hylogger).
- [GeoVIEW guide, TSG download workflow, printed page 69](https://geoview.dmp.wa.gov.au/Geoview/Help/GeoVIEW_Help.pdf).
- [NCI live WA catalogue](https://thredds.nci.org.au/thredds/catalog/rs07/WA/catalog.html).
- [GSWA quick-access workbook](https://www.wa.gov.au/media/133659/download?inline=), source of reported core lengths/projects.
- [NVCL data service](https://geossdi.dmp.wa.gov.au/NVCLDataServices/), source of downloaded scalar logs and dataset inventories.
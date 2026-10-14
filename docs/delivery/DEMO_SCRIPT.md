# HyLogger Explorer v2 — demo script

About 12 minutes. Internal: the GSWA interim package is not to be shown outside the agreed audience. Numbers below are from the live local data root; re-check any you quote if the analysis has been refreshed.

## Before you start (10 minutes before)

1. Stop the laptop from sleeping (the host worker dies when it sleeps).
2. Start the stack and the worker from `C:\HyLogger\explorer-v2`:

   ```powershell
   docker compose -f platform/docker-compose.yml up -d
   powershell -ExecutionPolicy Bypass -File scripts/start_worker.ps1 -Legacy "<old data dir>"
   python -m hylogger.jobs list
   ```

   The Data tab shows a green worker banner when it is running.
3. Warm the caches by opening once: `http://127.0.0.1:8100/#/map`, `#/clusters` (wait for the machine-vs-analyst panel), `#/map/WTB-60`, `#/hole/05GJD001/200-260`, `#/similarity`, `#/anomalies`, `#/prediction`.
4. Browser at 1280 px or wider. Internet is needed for the base map and for the live download; without it the holes still draw on a blank background — skip step 8.
5. Set the selection to `WTB-60, WTB-29, WTB-22, 13TRDD012, CCUD001, GG274` (Similarity → “Type or paste hole IDs”).

## 1 · Map — what exists (1.5 min)

- The six cards: 2,161 holes in the catalogue, 1,322 with mineral data, 1,357 with raw spectra summarised, 416k metres of SWIR and 384k of TIR. Hover a card for what it counts.
- Hover any hole: its three main SWIR and TIR minerals. The “target” line is the company’s exploration commodity, not a measurement.
- Click **WTB-60**: SWIR minerals, the raw spectrum with the Al-OH position (2195 nm → sodic/muscovitic white mica), and the ten most similar holes rise on the map with the reasons (“same order down the hole: Chlorite-FeMg → Paragonite; 85% of metres in the same clusters; less than 1 km, same district”).
- Switch “Most similar holes” to **Down-hole sequence** and to **Raw spectra**: the same neighbours (WTB-29, WTB-22) come back from independent evidence.
- Colour by **Machine vs analyst (SWIR)**: green holes are where the analyst kept the automatic interpretation.

## 2 · Hole — one hole in depth (2 min)

- Open `#/hole/05GJD001/200-260`. Tracks: SWIR/TIR mix, cluster ribbon, domains, unusualness, **auto vs reviewed** (sTSAS/uTSAS, sjCLST/ujCLST) with the Agree track, Al-OH position, assays and predicted chemistry, logged geology.
- Drag over any depth range: the right panel summarises minerals, clusters, assays, logged geology, the mean raw spectrum and auto vs reviewed for exactly that range; the range is kept in the URL.
- “Automatic vs reviewed” panel: in this hole the first mineral agrees in 94% of SWIR readings; the analyst changed the main mineral in 14 of 166 m (mostly to Aspectral).

## 3 · Compare holes (2.5 min)

- With the six holes in the set: the distance matrix (dark = similar) and what each contains.
- Section 4: tick WTB-60, WTB-29, WTB-22 and show the strip logs side by side.
- Section 5: align **WTB-60 ↔ WTB-29** (SWIR). Read it with the box on the left: chlorite → paragonite → chlorite zones match, the paragonite zone is shallower in WTB-29. Sequence distance ≈ 0.13 (“very similar sequence”).
- Section 6: pick a matched zone (e.g. WTB-60 188–211 m ↔ WTB-29 160–194 m) and **Compare spectra**: spectral angle ≈ 2° and the same Al-OH position — the raw spectra confirm the match independently of TSG.

## 4 · Clusters — recurring assemblages and how far to trust them (2 min)

- Cards: each cluster’s mix, typical depth and the holes where it is most common.
- Robustness: three checks, all **stable** — clusters hold for hidden districts (ARI ≈ 0.89 SWIR), for other interval widths, and from new random starts.
- Mineralogy vs chemistry: white mica ↔ K₂O ρ 0.52, carbonate ↔ LOI, plagioclase ↔ Na₂O, each with why it is expected.
- Virtual assays: MgO, CaO, Na₂O, K₂O, LOI, Cr, Al₂O₃, Ni predicted on unseen holes (R² 0.30–0.57); gold, uranium, sulphur and fine silica cannot be predicted — the HyLogger does not see their host minerals.
- **How often does the analyst change the machine’s answer?** SWIR 95.5% of readings agree (108 holes), TIR 87.0% (100 holes), VNIR 99.8% (31 holes). The main mineral group changes in only 2.9% (SWIR) and 5.8% (TIR) of metres: most changes are species within a group (ankerite ↔ dolomite, Fe ↔ FeMg chlorite) or the analyst rejecting a low-confidence match (Aspectral). In TIR the analyst most often replaces quartz (by chlorite or albite).

## 5 · Anomalies (1 min)

- Read the three boxes (score, checks, what it is not).
- Click the first metre (PLRCD001 419 m): jarosite 33% here vs 7% around it, far from its kaolinite cluster — an acid-alteration band or a sampling artefact, worth a look.
- Click **BUR2034** in unusual holes: 52% serpentine vs 1.4% in a typical hole; its closest holes are 740 km away.

## 6 · Prediction (1 min)

- Click near Kalgoorlie: the eight nearest holes rise with line width by weight; their own mixes sit next to the estimate; the depth table shows how each depth band is built.
- Be explicit about reliability: near known holes the estimate beats the regional average (skill 44%); in an unseen district it is only 8% better, and beyond ~20 km the app says it is a regional prior.

## 7 · Method (30 s)

Plain-language method and the active run cards. Credits: Sathi (abundances, K rule, fingerprints), Avantika (TIR diagnostics), Yeshwanth (raw whole-hole distance).

## 8 · Live: download a hole from the app (optional, 2–4 min, needs internet)

- Open `#/hole/WTB-07` → “Automatic vs reviewed” → **Download the raw spectra again to compare**. The Data tab shows the job: only the spectral members of the 290 MB package are fetched with byte ranges, summarised per metre, and the automatic vs reviewed comparison is extracted. Reload the hole when it finishes.
- If the server is slow (THREDDS can take ~20 s per request) move on and come back at the end.

## Likely questions

- *Is the uTSAS “truth”?* No: it is the analyst-reviewed run that NVCL publishes. Differences show where the analyst intervened, not necessarily automatic errors.
- *Why do raw and processed similarity differ (ρ ≈ 0.3 NIR)?* The raw spectra carry information the TSG mineral names do not (e.g. white-mica chemistry, water); the raw view complements the processed one.
- *Data licence?* Interim GSWA package: local only, not in Git, not hosted.
- *What is missing?* Automatic-vs-reviewed for holes summarised before 9 Oct needs a re-download (button on the Hole page); DASC mineral logs are not scripted (access control); Avantika’s TIR whole-hole notebook (PR #7) is not yet in the app.

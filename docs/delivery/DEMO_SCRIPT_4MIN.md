# HyLogger Explorer v2 — 4-minute demo

## Set up before (pre-open one browser tab per step, laptop sleep off)

1. `http://127.0.0.1:8100/#/map/WTB-60`
2. `http://127.0.0.1:8100/#/hole/05GJD001/200-260`
3. `http://127.0.0.1:8100/#/similarity` with the set `WTB-60, WTB-29, WTB-22, 13TRDD012`; press **Align** (WTB-60 ↔ WTB-29) and **Compare spectra** for the matched zone 188–211 m ↔ 160–194 m
4. `http://127.0.0.1:8100/#/clusters`, scrolled to “How often does the analyst change the machine’s answer?”

## Script

**0:00 — Hook (20 s).** “GSWA has HyLogger scans for about two thousand drill holes, but each one is looked at alone. We built a tool that puts every hole on one map, compares any number of them, and tells you why they are similar — and how far to trust it.”

**0:20 — Map (50 s), tab 1.** “2,161 holes; 1,322 with mineral logs and 1,357 with raw spectra we summarised ourselves — 645 of those have no published interpretation at all.” Hover a hole: “main minerals at a glance.” Point at WTB-60: “its ten most similar holes rise on the map, each with the reason: same minerals, same order down the hole, same white-mica chemistry in the raw spectra.”

**1:10 — Hole (50 s), tab 2.** “Every sensor on one depth scale.” Drag over a depth range: “select any interval and get its minerals, assays, geology and spectrum.” Point at the auto/reviewed tracks: “the machine’s interpretation next to the analyst’s.”

**2:00 — Compare (50 s), tab 3.** “Two neighbouring holes aligned zone by zone: chlorite, then paragonite, then chlorite — thicker in one, thinner in the other.” Scroll to the spectra: “and the raw spectra of the matched zones agree within about 2 degrees, independently of the interpretation.”

**2:50 — Trust (40 s), tab 4.** “How often does the analyst change the machine’s answer? On SWIR the first mineral agrees in 95% of 11 million readings; the mineral group changes in only 3% of metres. TIR is harder: 87%, mostly quartz relabelled as chlorite or albite.” “And the clusters are stable: they hold when whole districts are hidden.”

**3:30 — Close (30 s).** “It also flags unusual metres with the evidence, and predicts the expected mineralogy at a new location — and says honestly that beyond about 20 km from a hole it is only a regional guess. Everything runs locally; the interim data never leaves the machine. Next: bring in the remaining automatic-vs-reviewed comparisons and Avantika’s TIR whole-hole clustering.”

**4:00 — Stop. Use the remaining two minutes for questions.**

## If something is slow

Skip the live step, describe it, and move to the next pre-opened tab. Numbers to keep in mind: 2,161 / 1,322 / 1,357 holes; SWIR 95.5% (108 holes) and TIR 87.0% (100 holes) agreement; group change 2.9% (SWIR), 5.8% (TIR); support limit ~20 km.

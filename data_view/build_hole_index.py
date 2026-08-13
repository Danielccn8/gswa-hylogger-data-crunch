"""Fetch every WA HyLogger hole and save it as a small CSV.

This is the "precompute" step. The Streamlit app never calls the API — it just
reads this file. Run it whenever you want to refresh the list.

    python build_hole_index.py
"""
from pathlib import Path

import pandas as pd
from nvcl_kit.reader import NVCLReader
from nvcl_kit.param_builder import param_builder

OUT = Path(__file__).parent / "outputs"
OUT.mkdir(exist_ok=True)

print("Querying GSWA for every HyLogger hole in WA...")
reader = NVCLReader(param_builder("wa", max_boreholes=0))
holes = pd.DataFrame([vars(b) for b in reader.get_boreholes_list()])

# Keep only the columns worth carrying around, with readable names.
holes = holes.rename(columns={"nvcl_id": "hole_id", "x": "lon", "y": "lat",
                              "boreholeLength_m": "length_m",
                              "drillingMethod": "drilling_method"})
keep = ["hole_id", "lat", "lon", "length_m", "drilling_method",
        "operator", "project", "drillStartDate", "tenement"]
holes = holes[[c for c in keep if c in holes.columns]]

for col in ("lat", "lon", "length_m"):
    holes[col] = pd.to_numeric(holes[col], errors="coerce")

holes = holes.dropna(subset=["lat", "lon"]).sort_values("hole_id")

path = OUT / "wa_holes.csv"
holes.to_csv(path, index=False)
print(f"Saved {len(holes):,} holes -> {path}  ({path.stat().st_size / 1024:.0f} KB)")
print(holes.head().to_string(index=False))

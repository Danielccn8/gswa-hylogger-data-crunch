"""Run the bounded HDBSCAN comparison on existing interval products."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hylogger.comparators import compare_hdbscan  # noqa: E402


def main() -> None:
    root = ROOT / "data" / "system"
    for region in ("swir", "tir"):
        paths = [
            p
            for p in (root / "etl").glob("*/intervals.parquet")
            if pd.read_parquet(p, columns=["region"]).region.iloc[0] == region
        ]
        table = pd.concat((pd.read_parquet(p) for p in paths), ignore_index=True)
        result = compare_hdbscan(table)
        target = root / f"hdbscan_{region}.json"
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(target, result["candidates"])


if __name__ == "__main__":
    main()

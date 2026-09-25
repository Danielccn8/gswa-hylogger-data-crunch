"""Create read-only comparison artifacts for the reviewed 20-hole batch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hylogger.hole_comparison import compare_holes  # noqa: E402


def main() -> None:
    root = ROOT / "data" / "system"
    batch = json.loads((root / "benchmark_20_console.json").read_text(encoding="utf-8"))
    paths = [
        root / "etl" / entry["interval_set_id"] / "intervals.parquet"
        for entry in batch["completed"]
    ]
    absent = [str(path) for path in paths if not path.is_file()]
    if absent:
        raise FileNotFoundError(f"Missing reviewed ETL products: {absent}")
    intervals = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    destination = root / "comparisons"
    destination.mkdir(parents=True, exist_ok=True)
    for region in ("swir", "tir"):
        result = compare_holes(intervals, requested_holes=batch["requested_holes"], region=region)
        path = destination / f"team_20_{region}.json"
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "region": region,
                    "model_id": result["model_id"],
                    "included": result["included_holes"],
                    "excluded": result["excluded_holes"],
                    "selected_k": result["selected_k"],
                    "review_candidates": [
                        row["hole_id"] for row in result["holes"] if row["review_candidate"]
                    ],
                    "output": str(path),
                }
            )
        )


if __name__ == "__main__":
    main()

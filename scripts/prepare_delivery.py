"""Inventory the agreed cohort; optionally acquire missing compact API interpretations.

Usage: python scripts/prepare_delivery.py --data-dir DATA --output OUTPUT [--fetch]
Raw packages are inventoried, never bulk-downloaded by this script.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd

from hylogger.config import AppConfig
from hylogger.processed import fetch_processed_hole
from hylogger.util import atomic_write_json, sha256_file, utc_now


def run(config: AppConfig, output: Path, fetch: bool) -> dict:
    cohort = json.loads((config.repo_root / "configs/delivery_cohort.json").read_text())
    started = time.perf_counter()
    report = {
        "cohort": cohort,
        "created_at": utc_now(),
        "data_dir": str(config.data_dir),
        "holes": [],
    }
    names = ["Grp1 uTSAS", "Min1 uTSAS", "Grp1 ujCLST", "Min1 ujCLST", "Final Mask"]
    for region, holes in cohort["regions"].items():
        for hole in holes:
            item = {"hole_id": hole, "region": region}
            root = config.data_dir / "downloads"
            paths = sorted((root / "processed" / hole).glob("*/scalar_logs.parquet"))
            began = time.perf_counter()
            try:
                if fetch and not paths:
                    item["acquisition"] = fetch_processed_hole(config, hole, names=names)
                    paths = sorted((root / "processed" / hole).glob("*/scalar_logs.parquet"))
                item["processed"] = []
                for path in paths:
                    frame = pd.read_parquet(path)
                    item["processed"].append(
                        {
                            "path": str(path),
                            "sha256": sha256_file(path),
                            "rows": len(frame),
                            "bytes": path.stat().st_size,
                            "regions": [
                                r
                                for r, c in (("swir", "grp1_u_tsas"), ("tir", "grp1_uj_clst"))
                                if c in frame
                            ],
                        }
                    )
                raw = root / "raw" / hole
                item["raw_files"] = [
                    {"path": str(p), "bytes": p.stat().st_size}
                    for p in raw.rglob("*")
                    if p.is_file() and p.suffix.lower() in {".tsg", ".bip", ".zip"}
                ]
                item["raw_status"] = (
                    "requires_pair_and_numerical_QC" if item["raw_files"] else "not_found"
                )
                item["status"] = "available" if len(paths) == 1 else "missing_or_ambiguous"
            except Exception as exc:
                item.update(status="failed", error=str(exc))
            item["elapsed_seconds"] = round(time.perf_counter() - began, 3)
            report["holes"].append(item)
            report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            atomic_write_json(output / "cohort_inventory.json", report)
            print(hole, item["status"], item["elapsed_seconds"], flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    result = run(
        replace(AppConfig.load(data_dir=args.data_dir), retries=2), args.output, args.fetch
    )
    if any(hole["status"] != "available" for hole in result["holes"]):
        raise SystemExit(1)

"""Verify API/version/CSV/similarity semantics and real raw row-to-spectrum traces.

Run after the delivery ETL and local API startup. Writes evidence, never input data.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from hylogger.config import AppConfig
from hylogger.intervals import build_intervals
from hylogger.raw import find_package_dir
from hylogger.util import atomic_write_json


def verify(config: AppConfig, output: Path, base: str) -> dict:
    report = {"api": [], "raw": []}

    def get(route):
        response = requests.get(base + route, timeout=30)
        response.raise_for_status()
        return response.json()

    for region in ("swir", "tir"):
        started = time.perf_counter()
        dashboard = get(f"/api/system/dashboard?region={region}")
        assert len(dashboard["holes"]) == 20 and not dashboard["missing_holes"]
        sets = {h["hole_id"]: h["interval_set_id"] for h in dashboard["holes"]}
        for hole in dashboard["holes"]:
            assert sum(hole["primary_reasons"].values()) == hole["rows_received"]
            assert 0 <= hole["diagnostic_readings"] <= hole["readings"]
        hole = next(h for h in dashboard["holes"] if h["hole_id"] == "LD82625W1")
        route = f"/api/system/holes/LD82625W1/intervals?region={region}&limit=10000"
        profile = get(route)
        assert profile["next_offset"] is None
        assert len(profile["intervals"]) == hole["intervals"]
        assert {r["interval_set_id"] for r in profile["intervals"]} == {sets["LD82625W1"]}
        start = profile["intervals"][0]["depth_from_m"]
        query = (
            f"region={region}&interval_set_id={sets['LD82625W1']}"
            f"&model_id={dashboard['model_id']}&from_m={start}&to_m={start + 10}"
        )
        filtered = get(f"/api/system/holes/LD82625W1/intervals?{query}")
        response = requests.get(f"{base}/api/system/holes/LD82625W1/export.csv?{query}", timeout=30)
        response.raise_for_status()
        exported = list(csv.DictReader(io.StringIO(response.text)))
        assert exported
        assert [r["interval_id"] for r in exported] == [
            r["interval_id"] for r in filtered["intervals"]
        ]
        assert {r["model_id"] for r in exported} == {dashboard["model_id"]}
        chosen = next(r for r in profile["intervals"] if r["n_admitted"] > 0)
        similar = get(f"/api/system/intervals/{chosen['interval_id']}/similar")
        distances = []
        for match in similar["items"]:
            detail = get(f"/api/system/intervals/{match['interval_id']}")
            keys = set(chosen["features"]) | set(detail["features"])
            expected = math.sqrt(
                sum(
                    (
                        math.sqrt(chosen["features"].get(k) or 0)
                        - math.sqrt(detail["features"].get(k) or 0)
                    )
                    ** 2
                    for k in keys
                )
            )
            assert math.isclose(expected, match["distance"], abs_tol=1e-10)
            assert chosen["interval_id"] != match["interval_id"]
            distances.append(match["distance"])
        assert distances == sorted(distances)
        report["api"].append(
            {
                "region": region,
                "holes": 20,
                "csv_rows": len(exported),
                "similarity_verified": len(distances),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "model_id": dashboard["model_id"],
            }
        )
        atomic_write_json(output / "verification.json", report)

    from pytsg import parse_tsg

    for hole in ("LD82625W1", "WKDD15001"):
        package = parse_tsg.read_package(find_package_dir(config, hole), read_cras_file=False)
        for region, section_name in (("swir", "nir"), ("tir", "tir")):
            intervals = build_intervals(config, hole, region=region, remove_continuum=False)
            section = getattr(package, section_name)
            depths = pd.to_numeric(section.sampleheaders["D"], errors="coerce").to_numpy()
            masks = pd.to_numeric(section.scalars["Final Mask"], errors="coerce").to_numpy()
            verified = []
            for index in sorted({0, len(intervals) // 2, len(intervals) - 1}):
                row = intervals.table.iloc[index]
                indices = np.asarray(row.source_rows, dtype=int)
                assert np.all(masks[indices] == 1)
                assert np.all(depths[indices] >= row.depth_from)
                assert np.all(depths[indices] < row.depth_to)
                expected = np.median(section.spectra[indices], axis=0)
                np.testing.assert_allclose(intervals.spectra_raw[index], expected, rtol=1e-6)
                verified.append({"depth_from": float(row.depth_from), "source_rows": len(indices)})
            qc = intervals.qc
            assert sum(qc["primary_reasons"].values()) == qc["rows_received"]
            assert qc["rows_admitted"] == qc["readings_used"] + qc["readings_dropped_sparse"]
            report["raw"].append(
                {
                    "hole_id": hole,
                    "region": region,
                    "readings": qc["rows_received"],
                    "admitted": qc["rows_admitted"],
                    "intervals": len(intervals),
                    "paired_checks": verified,
                }
            )
            atomic_write_json(output / "verification.json", report)
            print(hole, region, "source-to-median verified", flush=True)
        del package
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/delivery-20261003"))
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    verify(AppConfig.load(data_dir=args.data_dir), args.output, args.api)

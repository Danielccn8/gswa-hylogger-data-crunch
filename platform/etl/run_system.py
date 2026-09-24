"""Local batch: TSG Parquet -> versioned intervals -> evaluated models -> PostGIS.

Example: python platform/etl/run_system.py --data-dir DATA --holes A B C --output data/system
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from hylogger.interval_model import fit, infer  # noqa: E402
from hylogger.system import REGIONS, interval_set, write_product  # noqa: E402


def load(conn: psycopg.Connection, table: pd.DataFrame, report: dict) -> None:
    """One transaction per complete set; retry is an idempotent upsert."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """INSERT INTO etl_run(interval_set_id,hole_id,dataset_id,region,source_sha256,qc)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(interval_set_id) DO UPDATE SET qc=EXCLUDED.qc""",
            (
                report["interval_set_id"],
                report["hole_id"],
                report["dataset_id"],
                report["region"],
                report["source_sha256"],
                Jsonb(report),
            ),
        )
        for row in table.to_dict("records"):
            cur.execute(
                """INSERT INTO interval_product
                (interval_id,interval_set_id,hole_id,dataset_id,region,depth_from_m,depth_to_m,
                 n_total,n_admitted,source_rows,source_depth_min_m,source_depth_max_m,
                 features,dominant_group,reason_counts)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(interval_id) DO UPDATE SET
                  n_total=EXCLUDED.n_total,n_admitted=EXCLUDED.n_admitted,
                  source_rows=EXCLUDED.source_rows,features=EXCLUDED.features,
                  dominant_group=EXCLUDED.dominant_group,
                  reason_counts=EXCLUDED.reason_counts""",
                (
                    row["interval_id"],
                    row["interval_set_id"],
                    row["hole_id"],
                    row["dataset_id"],
                    row["region"],
                    row["depth_from_m"],
                    row["depth_to_m"],
                    row["n_total"],
                    row["n_admitted"],
                    Jsonb(row["source_rows"]),
                    row["source_depth_min_m"],
                    row["source_depth_max_m"],
                    Jsonb(row["features"]),
                    row["dominant_group"] if pd.notna(row["dominant_group"]) else None,
                    Jsonb(row["reason_counts"]),
                ),
            )


def load_model(conn: psycopg.Connection, card: dict, assignments: pd.DataFrame) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """INSERT INTO model_run(model_id,region,card) VALUES(%s,%s,%s)
            ON CONFLICT(model_id) DO NOTHING""",
            (card["model_id"], card["region"], Jsonb(card)),
        )
        for row in assignments.to_dict("records"):
            cur.execute(
                """INSERT INTO interval_assignment(model_id,interval_id,cluster_id,distance,status)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(model_id,interval_id) DO UPDATE SET
                cluster_id=EXCLUDED.cluster_id,distance=EXCLUDED.distance,status=EXCLUDED.status""",
                (
                    row["model_id"],
                    row["interval_id"],
                    int(row["cluster_id"]) if pd.notna(row["cluster_id"]) else None,
                    float(row["distance"]) if pd.notna(row["distance"]) else None,
                    row["status"],
                ),
            )
        cur.execute(
            """INSERT INTO active_model(region,model_id) VALUES(%s,%s)
            ON CONFLICT(region) DO UPDATE SET model_id=EXCLUDED.model_id""",
            (card["region"], card["model_id"]),
        )


def load_inference(
    conn: psycopg.Connection, model_id: str, region: str, assignments: pd.DataFrame
) -> None:
    """Write assignments for a frozen model without changing the active version."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT region FROM model_run WHERE model_id=%s", (model_id,))
        found = cur.fetchone()
        if found is None or found[0] != region:
            raise ValueError(f"Model {model_id} is not registered for {region}")
        for row in assignments.to_dict("records"):
            cur.execute(
                """INSERT INTO interval_assignment(model_id,interval_id,cluster_id,distance,status)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(model_id,interval_id) DO UPDATE SET
                cluster_id=EXCLUDED.cluster_id,distance=EXCLUDED.distance,status=EXCLUDED.status""",
                (
                    model_id,
                    row["interval_id"],
                    int(row["cluster_id"]) if pd.notna(row["cluster_id"]) else None,
                    float(row["distance"]) if pd.notna(row["distance"]) else None,
                    row["status"],
                ),
            )


def run(
    data_dir: Path,
    holes: list[str],
    output: Path,
    database_url: str,
    width_m: float,
    mode: str = "train",
    model_ids: dict[str, str] | None = None,
) -> dict:
    import psutil

    started = time.perf_counter()
    peak_rss = [0]
    stop = threading.Event()

    def sample_memory() -> None:
        process = psutil.Process()
        while not stop.wait(0.1):
            peak_rss[0] = max(peak_rss[0], process.memory_info().rss)

    monitor = threading.Thread(target=sample_memory, daemon=True)
    monitor.start()
    output.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[pd.DataFrame]] = {region: [] for region in REGIONS}
    summary = {
        "batch_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
        "requested_holes": holes,
        "width_m": width_m,
        "mode": mode,
        "completed": [],
        "failed": [],
    }
    with psycopg.connect(database_url, autocommit=True) as conn:
        migration = (
            ROOT / "platform" / "db" / "migrations" / "002_interval_system.sql"
        ).read_text()
        conn.execute(migration)
        for hole in holes:
            paths = sorted(
                (data_dir / "downloads" / "processed" / hole).glob("*/scalar_logs.parquet")
            )
            if len(paths) != 1:
                summary["failed"].append(
                    {
                        "hole_id": hole,
                        "reason": f"expected one reviewed dataset, found {len(paths)}",
                    }
                )
                continue
            for region in REGIONS:
                try:
                    table, report = interval_set(paths[0], hole, region, width_m)
                    write_product(table, report, output / "etl")
                    load(conn, table, report)
                    tables[region].append(table)
                    summary["completed"].append(
                        {
                            "hole_id": hole,
                            "region": region,
                            "interval_set_id": report["interval_set_id"],
                            "rows": report["rows_received"],
                            "intervals": len(table),
                        }
                    )
                except Exception as exc:
                    summary["failed"].append(
                        {"hole_id": hole, "region": region, "reason": str(exc)}
                    )
        summary["etl_elapsed_seconds"] = round(time.perf_counter() - started, 3)
        model_started = time.perf_counter()
        for region, parts in tables.items():
            if len(parts) < 2:
                if mode == "train":
                    continue
            if not parts or mode == "etl-only":
                continue
            try:
                table = pd.concat(parts, ignore_index=True)
                if mode == "train":
                    card = fit(table, output / "models" / region)
                    assignments = pd.read_parquet(
                        output / "models" / region / card["model_id"] / "assignments.parquet"
                    )
                    load_model(conn, card, assignments)
                    summary.setdefault("models", {})[region] = card["model_id"]
                elif mode == "infer":
                    model_id = (model_ids or {}).get(region)
                    if not model_id:
                        raise ValueError(f"Frozen {region} model ID required for inference")
                    model_dir = output / "models" / region / model_id
                    valid = table.loc[table.n_admitted > 0].reset_index(drop=True)
                    if valid.empty:
                        continue
                    assignments = infer(valid, model_dir)
                    load_inference(conn, model_id, region, assignments)
                    summary.setdefault("models", {})[region] = model_id
                    summary.setdefault("inference_assignments", {})[region] = len(assignments)
            except Exception as exc:
                summary["failed"].append({"region": region, "stage": "model", "reason": str(exc)})
        summary["model_or_inference_elapsed_seconds"] = round(
            time.perf_counter() - model_started, 3
        )
    stop.set()
    monitor.join(timeout=1)
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    summary["peak_process_rss_bytes_sampled"] = peak_rss[0]
    summary["source_bytes"] = sum(
        p.stat().st_size
        for hole in holes
        for p in (data_dir / "downloads" / "processed" / hole).glob("*/scalar_logs.parquet")
    )
    summary["output_bytes_total_at_end"] = sum(
        p.stat().st_size for p in output.rglob("*") if p.is_file()
    )
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "INSERT INTO batch_run(batch_id,summary) VALUES(%s,%s)",
            (summary["batch_id"], Jsonb(summary)),
        )
    (output / "batches").mkdir(exist_ok=True)
    (output / "batches" / f"{summary['batch_id']}.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (output / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--holes", nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "system")
    parser.add_argument("--width-m", type=float, default=1.0)
    parser.add_argument("--mode", choices=("train", "infer", "etl-only"), default="train")
    parser.add_argument(
        "--infer-model-swir", help="Frozen SWIR model ID; required for --mode infer"
    )
    parser.add_argument("--infer-model-tir", help="Frozen TIR model ID; required for --mode infer")
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL", "postgresql://hylogger:hylogger@localhost:5433/hylogger"
        ),
    )
    args = parser.parse_args()
    if args.mode == "infer" and not (args.infer_model_swir or args.infer_model_tir):
        parser.error("--mode infer requires at least one --infer-model-* ID")
    print(
        json.dumps(
            run(
                args.data_dir,
                args.holes,
                args.output,
                args.database_url,
                args.width_m,
                args.mode,
                {"swir": args.infer_model_swir, "tir": args.infer_model_tir},
            ),
            indent=2,
        )
    )

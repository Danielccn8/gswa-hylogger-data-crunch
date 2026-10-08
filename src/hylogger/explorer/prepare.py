"""Prepare one hole: processed logs -> mineral interval products -> database."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .db import copy_frame
from .features import SENSORS, MissingLogs, interval_features, select_dataset, write_product

DB_COLUMNS = [
    "feature_set_id",
    "bin",
    "hole_id",
    "sensor",
    "depth_from_m",
    "depth_to_m",
    "n_total",
    "n_admitted",
    "minerals",
    "groups",
    "dominant_mineral",
    "dominant_group",
    "error_mean",
]


def products_root(data_dir: Path) -> Path:
    return data_dir / "products" / "features"


def load_feature_set(conn: psycopg.Connection, table, report: dict, product: Path) -> None:
    """Idempotent: replace this set's rows and make it the active set for hole/sensor."""
    with conn.transaction():
        conn.execute(
            """INSERT INTO feature_set(feature_set_id,hole_id,dataset_id,sensor,width_m,qc_version,
               source_sha256,qc,product_path) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (feature_set_id) DO UPDATE SET qc=EXCLUDED.qc,
               product_path=EXCLUDED.product_path""",
            (
                report["feature_set_id"],
                report["hole_id"],
                report["dataset_id"],
                report["sensor"],
                report["width_m"],
                report["qc_version"],
                report["source_sha256"],
                Jsonb(report),
                str(product),
            ),
        )
        conn.execute(
            "DELETE FROM interval_feature WHERE feature_set_id=%s", (report["feature_set_id"],)
        )
        copy_frame(conn, "interval_feature", table[DB_COLUMNS], json_columns=("minerals", "groups"))
        # Superseded revisions keep their feature_set record and Parquet product on disk;
        # only their interval rows leave the database, so queries never pool revisions.
        conn.execute(
            """DELETE FROM interval_feature WHERE feature_set_id IN (
                   SELECT feature_set_id FROM feature_set
                   WHERE hole_id=%s AND sensor=%s AND feature_set_id<>%s)""",
            (report["hole_id"], report["sensor"], report["feature_set_id"]),
        )
        conn.execute(
            """INSERT INTO active_feature_set(hole_id,sensor,feature_set_id) VALUES (%s,%s,%s)
               ON CONFLICT (hole_id,sensor) DO UPDATE SET feature_set_id=EXCLUDED.feature_set_id,
               selected_at=now()""",
            (report["hole_id"], report["sensor"], report["feature_set_id"]),
        )


def prepare_hole(
    conn: psycopg.Connection, data_dir: Path, hole_id: str, *, width_m: float = 1.0
) -> dict:
    source = select_dataset(data_dir / "downloads" / "processed" / hole_id)
    if source is None:
        raise FileNotFoundError(f"No processed logs downloaded for {hole_id}")
    result: dict = {"hole_id": hole_id, "dataset_id": source.parent.name, "sensors": {}}
    for sensor in SENSORS:
        try:
            table, report = interval_features(source, hole_id, sensor, width_m)
        except MissingLogs:
            result["sensors"][sensor] = "no_logs"
            continue
        if table.empty:
            result["sensors"][sensor] = "no_intervals"
            continue
        product = write_product(table, report, products_root(data_dir))
        load_feature_set(conn, table, report, product)
        result["sensors"][sensor] = {
            "feature_set_id": report["feature_set_id"],
            "intervals": report["intervals"],
            "with_minerals": report["intervals_with_minerals"],
        }
    prepared = any(isinstance(value, dict) for value in result["sensors"].values())
    conn.execute(
        "UPDATE explorer_hole SET processed_status=%s, updated_at=now() WHERE hole_id=%s",
        ("prepared" if prepared else "no_usable_logs", hole_id),
    )
    conn.commit()
    return result

"""Load the WA hole index into Postgres.

    python etl/load_holes.py

Reads ../data_view/outputs/wa_holes.csv (produced by build_hole_index.py) and
upserts it into the `hole` table. Safe to run repeatedly.

This is the ETL layer. It is the ONLY place that talks to the outside world.
The API never calls GSWA — it only reads what this script wrote.
"""
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://hylogger:hylogger@localhost:5433/hylogger"
)

HERE = Path(__file__).resolve().parent
DEFAULT_CSV = HERE.parent.parent / "data_view" / "outputs" / "wa_holes.csv"


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)

    required = {"hole_id", "lat", "lon"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"{path} is missing required columns: {sorted(missing)}")

    for col in ("lat", "lon", "length_m"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # A hole with no coordinates cannot go on a map. Report it, don't hide it.
    no_coords = df["lat"].isna() | df["lon"].isna()
    if no_coords.any():
        print(f"  skipping {no_coords.sum()} holes with no coordinates")
        df = df[~no_coords]

    # Flag implausible lengths instead of dropping or silently correcting them.
    df["length_is_valid"] = df["length_m"].notna() & (df["length_m"] > 0)
    bad = (~df["length_is_valid"]).sum()
    if bad:
        print(f"  {bad} holes have a missing or invalid length — loaded and flagged")

    for col in ("drilling_method", "operator", "project", "tenement", "drillStartDate"):
        if col not in df:
            df[col] = None

    return df


def upsert(df: pd.DataFrame, conn) -> None:
    rows = [
        (
            r.hole_id, float(r.lat), float(r.lon),
            None if pd.isna(r.length_m) else float(r.length_m),
            bool(r.length_is_valid),
            _clean(r.drilling_method), _clean(r.operator),
            _clean(r.project), _clean(r.tenement), _clean(r.drillStartDate),
        )
        for r in df.itertuples()
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO hole (hole_id, lat, lon, geom, length_m, length_is_valid,
                              drilling_method, operator, project, tenement,
                              drill_start_date)
            VALUES (%s, %s, %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                    %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hole_id) DO UPDATE SET
                lat = EXCLUDED.lat,
                lon = EXCLUDED.lon,
                geom = EXCLUDED.geom,
                length_m = EXCLUDED.length_m,
                length_is_valid = EXCLUDED.length_is_valid,
                drilling_method = EXCLUDED.drilling_method,
                operator = EXCLUDED.operator,
                project = EXCLUDED.project,
                tenement = EXCLUDED.tenement,
                drill_start_date = EXCLUDED.drill_start_date,
                loaded_at = now()
            """,
            # ST_MakePoint takes (lon, lat) — in that order. Swapping them puts
            # every hole in WA somewhere off the coast of Somalia.
            [(h, la, lo, lo, la, *rest) for (h, la, lo, *rest) in rows],
        )
    conn.commit()


def _clean(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def main() -> None:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.exists():
        raise SystemExit(
            f"{csv_path} not found.\n"
            "Run this first:  python ../data_view/build_hole_index.py"
        )

    print(f"Reading {csv_path}")
    df = load_csv(csv_path)
    print(f"  {len(df):,} holes with coordinates")

    print(f"Connecting to {DATABASE_URL.rsplit('@', 1)[-1]}")
    with psycopg.connect(DATABASE_URL) as conn:
        upsert(df, conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE length_is_valid) FROM hole")
            total, valid = cur.fetchone()

    print(f"Done. {total:,} holes in the database ({valid:,} with a usable length).")


if __name__ == "__main__":
    main()

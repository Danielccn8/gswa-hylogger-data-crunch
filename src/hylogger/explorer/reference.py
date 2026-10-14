"""Load the GSWA HyLogger Dataset Package (SQLite) and build the statewide hole register.

The package is interim, not QA/QC'd and licensed for internal use only. It is
read from the data root and never copied into the repository. Values are loaded
as published, except the documented -999 missing-value marker, which becomes
NULL in every table.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from sklearn.cluster import DBSCAN

from .db import copy_frame, read_frame

TABLES = {
    "hylogger_dataset_index": "ref_index",
    "wamex_collar": "ref_collar",
    "wamex_survey": "ref_survey",
    "wamex_dh_geology": "ref_geology",
    "wamex_assay_flat_data": "ref_assay",
    "wamex_assay_flat_metadata": "ref_assay_meta",
    "wapims_collar": "ref_wapims_collar",
    "wapims_survey": "ref_wapims_survey",
    "wapims_lithology": "ref_wapims_lithology",
}
INDEXES = {
    "ref_index": ["WAMEX_CollarId"],
    "ref_collar": ["CollarId"],
    "ref_survey": ["CollarId"],
    "ref_geology": ["CollarId"],
    "ref_assay": ["CollarId"],
    "ref_assay_meta": ["CollarId"],
    "ref_wapims_survey": ["WellId"],
    "ref_wapims_lithology": ["BoreholeId"],
}
SQL_TYPES = {"INTEGER": "bigint", "REAL": "double precision", "TEXT": "text"}
DISTRICT_RADIUS_KM = 5.0


def key(value: object) -> str:
    """Normalised hole key: upper-case letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper()) if pd.notna(value) else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 22):
            digest.update(chunk)
    return digest.hexdigest()


def load_tables(conn: psycopg.Connection, sqlite_path: Path) -> dict[str, int]:
    """Replace every ref_* table with the package contents, in one transaction."""
    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    counts: dict[str, int] = {}
    with conn.transaction():
        for sqlite_table, target in TABLES.items():
            info = source.execute(f'PRAGMA table_info("{sqlite_table}")').fetchall()
            columns = [(row[1], SQL_TYPES.get((row[2] or "TEXT").upper(), "text")) for row in info]
            frame = pd.read_sql(f'SELECT * FROM "{sqlite_table}"', source)
            for name, sql_type in columns:
                if sql_type != "text":
                    frame[name] = pd.to_numeric(frame[name], errors="coerce").mask(
                        lambda s: s == -999
                    )
                    if sql_type == "bigint":
                        frame[name] = frame[name].astype("Int64")
                else:
                    # Some source text lost its en dash in export; restore a readable one.
                    frame[name] = frame[name].astype("string").str.strip().str.replace("�", "–")
            conn.execute(f"DROP TABLE IF EXISTS {target} CASCADE")
            ddl = ", ".join(f'"{name}" {sql_type}' for name, sql_type in columns)
            conn.execute(f"CREATE TABLE {target} ({ddl})")
            counts[target] = copy_frame(conn, target, frame)
            for column in INDEXES.get(target, []):
                conn.execute(f'CREATE INDEX ON {target} ("{column}")')
        meta = pd.read_sql("SELECT * FROM meta_build", source)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ref_load (loaded_at timestamptz DEFAULT now(), "
            "sqlite_path text, sha256 text, build jsonb, row_counts jsonb)"
        )
        conn.execute(
            "INSERT INTO ref_load(sqlite_path, sha256, build, row_counts) VALUES (%s,%s,%s,%s)",
            (
                str(sqlite_path),
                sha256(sqlite_path),
                psycopg.types.json.Jsonb(dict(zip(meta.Key, meta.Value, strict=True))),
                psycopg.types.json.Jsonb(counts),
            ),
        )
    source.close()
    return counts


def _districts(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Single-linkage groups of collars within DISTRICT_RADIUS_KM of each other."""
    labels = np.full(len(lat), -1)
    ok = np.isfinite(lat) & np.isfinite(lon)
    if ok.sum():
        coords = np.radians(np.c_[lat[ok], lon[ok]])
        model = DBSCAN(eps=DISTRICT_RADIUS_KM / 6371.0, min_samples=1, metric="haversine")
        labels[ok] = model.fit_predict(coords)
    return labels


def build_register(conn: psycopg.Connection, catalog: pd.DataFrame) -> dict[str, int]:
    """Union of the NVCL/raw catalogue and the package index, keyed by catalogue hole ID."""
    if conn.execute("SELECT to_regclass('ref_index')").fetchone()[0] is None:
        return build_public_register(conn, catalog)
    index = read_frame(conn, 'SELECT * FROM ref_index ORDER BY "HyloggerIndexId"')
    collars = read_frame(conn, "SELECT * FROM ref_collar")
    wapims = read_frame(conn, "SELECT * FROM ref_wapims_collar")
    counts = {
        name: read_frame(
            conn, f'SELECT "CollarId" AS collar, count(*) AS n FROM {table} GROUP BY 1'
        ).set_index("collar")["n"]
        for name, table in (
            ("n_assay", "ref_assay"),
            ("n_geology", "ref_geology"),
            ("n_survey", "ref_survey"),
        )
    }
    index["collar"] = pd.to_numeric(index.WAMEX_CollarId, errors="coerce")
    alternate = pd.to_numeric(index.WAMEX_CollarId_alternate, errors="coerce")
    index["collar"] = index.collar.fillna(alternate)
    index["has_collar"] = index.collar.notna()
    index["k_nvcl"] = index.NVCL_HoleName.map(key)
    index["k_hole"] = index.Hole_name.map(key)
    index["k_data"] = index.Data_name.map(key)
    # Prefer index rows that resolve to a WAMEX collar, then the earliest row.
    index = index.sort_values(["has_collar", "HyloggerIndexId"], ascending=[False, True])

    lookups = [
        ("nvcl_name", index.drop_duplicates("k_nvcl").set_index("k_nvcl")),
        ("hole_name", index.drop_duplicates("k_hole").set_index("k_hole")),
        ("data_name", index.drop_duplicates("k_data").set_index("k_data")),
    ]
    rows = []
    used: set[int] = set()
    for record in catalog.itertuples(index=False):
        hole_key = key(record.hole_id)
        match, method = None, None
        for name, table in lookups:
            if hole_key and hole_key in table.index:
                match, method = table.loc[hole_key], name
                break
        if match is not None:
            used.add(int(match.HyloggerIndexId))
        rows.append(_register_row(record._asdict(), match, method))
    for _, record in index[~index.HyloggerIndexId.isin(used)].drop_duplicates("k_data").iterrows():
        hole_id = record.NVCL_HoleName if pd.notna(record.NVCL_HoleName) else record.Data_name
        rows.append(_register_row({"hole_id": hole_id}, record, "index_only"))
    register = pd.DataFrame(rows).drop_duplicates("hole_id")

    collars = collars.set_index("CollarId")
    register["wamex_collar_id"] = register.wamex_collar_id.astype("Int64")
    known = register.wamex_collar_id.isin(collars.index)
    linked = collars.reindex(register.wamex_collar_id.where(known))
    register["elevation_m"] = linked.Elevation.to_numpy()
    register["hole_type"] = linked.HoleType.to_numpy()
    register["tectonic_region"] = register.tectonic_region.fillna(
        pd.Series(linked.TectonicRegion.to_numpy(), index=register.index)
    )
    register["lat"] = register.lat.fillna(
        pd.Series(linked.Latitude.to_numpy(), index=register.index)
    )
    register["lon"] = register.lon.fillna(
        pd.Series(linked.Longitude.to_numpy(), index=register.index)
    )
    register.loc[known, "link_method"] = register.loc[known, "link_method"] + "+collar"
    wapims["k"] = wapims.HoleName.map(key)
    wapims = wapims.drop_duplicates("k").set_index("k")
    wkeys = register.name.map(key)
    has_well = wkeys.isin(wapims.index) & register.elevation_m.isna()
    register.loc[has_well, "elevation_m"] = wapims.loc[
        wkeys[has_well], "GroundElevation"
    ].to_numpy()
    for name, series in counts.items():
        register[name] = register.wamex_collar_id.map(series).fillna(0).astype(int)
    register["district_id"] = _districts(register.lat.to_numpy(float), register.lon.to_numpy(float))

    columns = [
        "hole_id",
        "name",
        "lat",
        "lon",
        "elevation_m",
        "length_m",
        "nvcl_available",
        "raw_available",
        "raw_size_bytes",
        "operator",
        "project",
        "drilling_method",
        "hole_type",
        "commodity",
        "tectonic_region",
        "source_database",
        "index_id",
        "data_name",
        "wamex_collar_id",
        "link_method",
        "scan_swir",
        "scan_tir",
        "n_assay",
        "n_geology",
        "n_survey",
        "mosaic_url",
        "summary_histogram_url",
        "summary_report_url",
        "mineral_log_url",
        "district_id",
    ]
    register = register[columns]
    register["raw_size_bytes"] = register.raw_size_bytes.astype("Int64")
    register["index_id"] = register.index_id.astype("Int64")
    with conn.transaction():
        status = read_frame(
            conn, "SELECT hole_id, processed_status, raw_status FROM explorer_hole"
        ).set_index("hole_id")
        conn.execute("DELETE FROM explorer_hole")
        copy_frame(conn, "explorer_hole", register)
        conn.execute(
            "UPDATE explorer_hole SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography "
            "WHERE lat IS NOT NULL AND lon IS NOT NULL"
        )
        if len(status):
            for hole_id, row in status.iterrows():
                conn.execute(
                    "UPDATE explorer_hole SET processed_status=%s, raw_status=%s WHERE hole_id=%s",
                    (row.processed_status, row.raw_status, hole_id),
                )
    return {
        "holes": len(register),
        "linked_to_index": int(register.index_id.notna().sum()),
        "linked_to_collar": int(register.wamex_collar_id.isin(collars.index).sum()),
        "with_assays": int((register.n_assay > 0).sum()),
        "districts": int(register.district_id.max() + 1),
    }


def build_public_register(conn: psycopg.Connection, catalog: pd.DataFrame) -> dict[str, int]:
    """Bootstrap from public services without the separately licensed GSWA package.

    Upsert catalogue attributes only: completed products and their status survive refreshes.
    A temporarily absent source never deletes locally acquired holes.
    """
    frame = catalog.copy()
    districts = _districts(frame.lat.to_numpy(float), frame.lon.to_numpy(float))
    for record, district in zip(frame.to_dict("records"), districts, strict=True):
        row = _register_row(record, None, "public_catalog")
        columns = [
            "hole_id",
            "name",
            "lat",
            "lon",
            "length_m",
            "nvcl_available",
            "raw_available",
            "raw_size_bytes",
            "operator",
            "project",
            "drilling_method",
        ]
        values = [None if pd.isna(row.get(c)) else row.get(c) for c in columns]
        values.append(int(district))
        columns.append("district_id")
        update = ", ".join(f"{c}=EXCLUDED.{c}" for c in columns if c != "hole_id")
        conn.execute(
            f"INSERT INTO explorer_hole({','.join(columns)}) "
            f"VALUES ({','.join(['%s'] * len(columns))}) ON CONFLICT(hole_id) "
            f"DO UPDATE SET {update}, updated_at=now()",
            values,
        )
    conn.execute(
        "UPDATE explorer_hole SET geom=ST_SetSRID(ST_MakePoint(lon,lat),4326)::geography "
        "WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180"
    )
    conn.commit()
    return {
        "holes": len(frame),
        "linked_to_index": 0,
        "linked_to_collar": 0,
        "with_assays": 0,
        "districts": len(set(districts)),
    }


def _register_row(catalog: dict, match: pd.Series | None, method: str | None) -> dict:
    def pick(name: str) -> object:
        return None if match is None else match.get(name)

    def text(value: object) -> object:
        return None if value is None or (isinstance(value, float) and np.isnan(value)) else value

    lat = catalog.get("lat")
    lon = catalog.get("lon")
    if lat is None or pd.isna(lat):
        lat, lon = pick("Shapefile_latitude"), pick("Shapefile_longitude")
    commodity = text(pick("Commodity_vocab")) or text(pick("COMMODITY"))
    return {
        "hole_id": catalog["hole_id"],
        "name": text(pick("Hole_name")) or catalog["hole_id"],
        "lat": lat,
        "lon": lon,
        "length_m": catalog.get("length_m"),
        "nvcl_available": bool(catalog.get("nvcl_available") or False),
        "raw_available": bool(catalog.get("raw_available") or False),
        "raw_size_bytes": catalog.get("raw_size_bytes"),
        "operator": text(catalog.get("operator")) or text(pick("DRILL_OPER")),
        "project": text(catalog.get("project")) or text(pick("PROJECT")),
        "drilling_method": text(catalog.get("drilling_method")),
        "commodity": commodity,
        "tectonic_region": None,
        "source_database": text(pick("Database")),
        "index_id": pick("HyloggerIndexId"),
        "data_name": text(pick("Data_name")),
        "wamex_collar_id": pick("collar"),
        "link_method": method or "unlinked",
        "scan_swir": text(pick("SCAN_DATE")),
        "scan_tir": text(pick("SCDATE_TIR")),
        "mosaic_url": text(pick("MOSAIC")),
        "summary_histogram_url": text(pick("SUM_HISTO")),
        "summary_report_url": text(pick("SUM_REPORT")),
        "mineral_log_url": text(pick("MINERALLOG")),
    }

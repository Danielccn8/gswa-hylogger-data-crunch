"""HyLogger Explorer API (v2).

    uvicorn --app-dir platform backend.main:app --port 8100

Reads precomputed, versioned results from PostGIS and the data root, and
enqueues background jobs (acquisition, preparation, analysis) for the worker.
No model is trained and no remote service is called inside a request; the
prediction endpoint only interpolates stored hole compositions.
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import time
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from hylogger.explorer import prediction as predict_lib  # noqa: E402
from hylogger.explorer.chemistry import ANALYTES, OXIDES, assays  # noqa: E402
from hylogger.jobs.queue import LANES  # noqa: E402

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://hylogger:hylogger@127.0.0.1:5434/hylogger"
)
DATA_DIR = Path(os.environ.get("HYLOGGER_DATA_DIR", "C:/HyLoggerData"))
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
SENSORS = ("swir", "tir", "vnir")
HOLE_VIEWS = (
    "combined",
    "swir",
    "tir",
    "raw_nir",
    "raw_tir",
    "seq_combined",
    "seq_swir",
    "seq_tir",
)
MAX_JOB_HOLES = 2500

pool = ConnectionPool(
    DATABASE_URL, min_size=1, max_size=12, kwargs={"row_factory": dict_row}, open=False
)
app = FastAPI(title="HyLogger Explorer", version="2.0.0")
app.add_middleware(GZipMiddleware, minimum_size=2048, compresslevel=5)


@app.middleware("http")
async def _revalidate_static(request, call_next):
    """Static modules revalidate (cheap with ETag) so an update is never served stale."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.on_event("startup")
def _open_pool() -> None:
    pool.open(wait=False)


def query(sql: str, params: object = None) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def one(sql: str, params: object = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def resolve(path: str | None) -> Path | None:
    """Map a stored product path (host or container) onto this process's data root."""
    if not path:
        return None
    parts = (
        PureWindowsPath(path) if "\\" in path or ":" in path[:3] else PurePosixPath(path)
    ).parts
    if "products" in parts:
        return DATA_DIR.joinpath(*parts[parts.index("products") :])
    return Path(path)


def clean(value):
    """JSON-safe: NaN/inf -> None, numpy -> python."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def active_run(kind: str, sensor: str) -> dict | None:
    return one(
        "SELECT run_id, card, product_path, created_at FROM analysis_run "
        "WHERE kind=%s AND sensor=%s AND status='active'",
        (kind, sensor),
    )


@lru_cache(maxsize=32)
def _distance(run_id: str, path: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    archive = np.load(resolve(path) / "distance.npz")
    return archive["names"].tolist(), archive["distance"], archive["leaves"]


@lru_cache(maxsize=16)
def _parquet(run_id: str, path: str, name: str) -> pd.DataFrame:
    return pd.read_parquet(resolve(path) / name)


# ----------------------------------------------------------------- overview
@app.get("/api/v2/health")
def health():
    try:
        row = one("SELECT count(*) AS holes FROM explorer_hole")
        return {"status": "ok", "holes": row["holes"]}
    except Exception as error:  # surfaced to the UI banner
        raise HTTPException(503, f"database unavailable: {error}") from error


@app.get("/api/v2/summary")
def summary():
    counts = one(
        """SELECT count(*) AS holes,
                  count(*) FILTER (WHERE nvcl_available) AS nvcl,
                  count(*) FILTER (WHERE raw_available) AS raw,
                  count(*) FILTER (WHERE n_assay > 0) AS with_assays,
                  count(*) FILTER (WHERE n_geology > 0) AS with_geology,
                  count(*) FILTER (WHERE processed_status='prepared') AS prepared,
                  count(*) FILTER (WHERE processed_status='no_logs') AS no_logs,
                  count(*) FILTER (WHERE raw_status LIKE 'summarised%%') AS raw_summarised,
                  count(DISTINCT district_id) AS districts
           FROM explorer_hole"""
    )
    intervals = query(
        """SELECT f.sensor, count(*) AS intervals, count(DISTINCT f.hole_id) AS holes,
                  sum(f.n_total) AS readings
           FROM interval_feature f JOIN active_feature_set a USING (feature_set_id)
           GROUP BY f.sensor"""
    )
    runs = query(
        "SELECT kind, sensor, run_id, created_at FROM analysis_run WHERE status='active' "
        "ORDER BY kind, sensor"
    )
    jobs = one(
        "SELECT count(*) FILTER (WHERE status='running') AS running, "
        "count(*) FILTER (WHERE status='queued') AS queued FROM job"
    )
    workers = query(
        "SELECT name, lanes, seen_at FROM worker WHERE seen_at > now() - interval '2 minutes'"
    )
    reference = one(
        "SELECT loaded_at, build->>'built_utc' AS built FROM ref_load ORDER BY loaded_at DESC LIMIT 1"
    )
    return clean(
        {
            "counts": counts,
            "intervals": intervals,
            "runs": runs,
            "jobs": jobs,
            "workers": workers,
            "reference": reference,
        }
    )


@app.get("/api/v2/map")
def map_holes(
    view: str = Query(
        "combined", pattern="^(combined|swir|tir|raw_nir|raw_tir|seq_combined|seq_swir|seq_tir)$"
    ),
):
    run = active_run("hole", view)
    rows = query(
        """SELECT h.hole_id, h.name, h.lat, h.lon, h.commodity, h.tectonic_region, h.district_id,
                  h.nvcl_available, h.raw_available, h.n_assay, h.n_geology, h.processed_status,
                  h.raw_status, h.length_m, h.operator, h.project, h.source_database,
                  p.cluster_id, p.anomaly_score
           FROM explorer_hole h
           LEFT JOIN hole_profile p ON p.hole_id=h.hole_id AND p.run_id=%s
           WHERE h.lat IS NOT NULL ORDER BY h.hole_id""",
        (run["run_id"] if run else None,),
    )
    agreement = _tsa_overview()["holes"]
    tops: dict[str, dict] = {}
    for sensor in ("swir", "tir"):
        mix_run = active_run("hole", sensor)
        if mix_run is None:
            continue
        for r in query(
            "SELECT hole_id, summary->'top_minerals' AS top FROM hole_profile WHERE run_id=%s",
            (mix_run["run_id"],),
        ):
            top = sorted((r["top"] or {}).items(), key=lambda kv: -kv[1])[:3]
            tops.setdefault(r["hole_id"], {})[sensor] = [[m, round(v, 2)] for m, v in top]
    for row in rows:
        row["swir_top"] = tops.get(row["hole_id"], {}).get("swir")
        row["tsa_agreement"] = agreement.get(row["hole_id"], {}).get("swir")
        row["tir_top"] = tops.get(row["hole_id"], {}).get("tir")
    columns = list(rows[0].keys()) if rows else []
    return clean(
        {
            "view": view,
            "run_id": run["run_id"] if run else None,
            "clusters": run["card"].get("hole_clustering", {}).get("k") if run else None,
            "cluster_names": run["card"].get("hole_clustering", {}).get("names", {}) if run else {},
            "columns": columns,
            "rows": [[r[c] for c in columns] for r in rows],
        }
    )


# ----------------------------------------------------------------- one hole
@app.get("/api/v2/holes/{hole_id}")
def hole(hole_id: str):
    record = one("SELECT * FROM explorer_hole WHERE hole_id=%s", (hole_id,))
    if record is None:
        raise HTTPException(404, "unknown hole")
    record.pop("geom", None)
    profiles = {}
    for view in HOLE_VIEWS:
        run = active_run("hole", view)
        if run is None:
            continue
        row = one(
            "SELECT cluster_id, anomaly_score, anomaly_reasons, summary FROM hole_profile "
            "WHERE run_id=%s AND hole_id=%s",
            (run["run_id"], hole_id),
        )
        if row:
            profiles[view] = row
    sensors = query(
        "SELECT a.sensor, s.dataset_id, s.qc FROM active_feature_set a "
        "JOIN feature_set s USING (feature_set_id) WHERE a.hole_id=%s",
        (hole_id,),
    )
    raw = query(
        "SELECT sensor, n_intervals, depth_from_m, depth_to_m, summary, source FROM raw_product "
        "WHERE hole_id=%s",
        (hole_id,),
    )
    return clean(
        {
            "hole": record,
            "profiles": profiles,
            "sensors": {
                s["sensor"]: {
                    "dataset_id": s["dataset_id"],
                    "intervals": s["qc"].get("intervals"),
                    "with_minerals": s["qc"].get("intervals_with_minerals"),
                    "reasons": s["qc"].get("primary_reasons"),
                    "mask_status": s["qc"].get("mask_status"),
                }
                for s in sensors
            },
            "raw": raw,
        }
    )


def _raw_bins(hole_id: str) -> dict:
    out = {}
    for row in query("SELECT sensor, product_path FROM raw_product WHERE hole_id=%s", (hole_id,)):
        path = resolve(row["product_path"]) / f"raw_{row['sensor']}.parquet"
        if path.exists():
            table = pd.read_parquet(path)
            run = active_run("hole", f"raw_{row['sensor']}")
            if run:
                classes_path = resolve(run["product_path"]) / "interval_classes.parquet"
                if classes_path.exists():
                    classes = _parquet(
                        run["run_id"], run["product_path"], "interval_classes.parquet"
                    )
                    classes = classes[classes.hole_id == hole_id][["bin", "spectral_class"]]
                    table = table.merge(classes, on="bin", how="left")
            out[row["sensor"]] = table.drop(columns=["hole_id", "sensor"]).to_dict("list")
    return out


@app.get("/api/v2/holes/{hole_id}/striplog")
def striplog(hole_id: str):
    tracks = {}
    for sensor in SENSORS:
        run = active_run("interval", sensor)
        rows = query(
            """SELECT f.feature_set_id, f.bin, f.depth_from_m, f.depth_to_m, f.n_total, f.n_admitted,
                      f.groups, f.minerals, f.dominant_mineral, f.error_mean,
                      l.cluster_id, l.coarse_cluster_id, l.domain_id, l.anomaly_score,
                      l.anomaly_reasons
               FROM interval_feature f JOIN active_feature_set a USING (feature_set_id)
               LEFT JOIN interval_label l ON l.run_id=%s AND l.feature_set_id=f.feature_set_id
                    AND l.bin=f.bin
               WHERE f.hole_id=%s AND f.sensor=%s ORDER BY f.bin""",
            (run["run_id"] if run else None, hole_id, sensor),
        )
        if not rows:
            continue
        for row in rows:
            minerals = row.pop("minerals") or {}
            row["top_minerals"] = dict(sorted(minerals.items(), key=lambda kv: -kv[1])[:5])
        domains = query(
            "SELECT domain_id, depth_from_m, depth_to_m, cluster_id, minerals FROM domain "
            "WHERE run_id=%s AND hole_id=%s ORDER BY depth_from_m",
            (run["run_id"] if run else None, hole_id),
        )
        names = {}
        if run:
            names = {
                (r["level"], r["cluster_id"]): r["name"]
                for r in query(
                    "SELECT level, cluster_id, name FROM cluster_profile WHERE run_id=%s",
                    (run["run_id"],),
                )
            }
        tracks[sensor] = {
            "run_id": run["run_id"] if run else None,
            "intervals": rows,
            "domains": domains,
            "cluster_names": {f"{level}:{cid}": name for (level, cid), name in names.items()},
        }
    record = one(
        "SELECT wamex_collar_id, raw_status FROM explorer_hole WHERE hole_id=%s", (hole_id,)
    )
    raw_status = (record or {}).get("raw_status")
    chemistry, lithology = [], []
    if record and record["wamex_collar_id"]:
        with pool.connection() as conn:
            chem = assays(conn, [int(record["wamex_collar_id"])])
        chem = chem.sort_values("f")
        chemistry = [
            {
                k: v
                for k, v in row.items()
                if v is not None and not (isinstance(v, float) and math.isnan(v))
            }
            for row in chem.drop(columns="collar_id").to_dict("records")
        ]
        lithology = query(
            """SELECT "FromDepth" AS f, "ToDepth" AS t, "CompanyLithologyCodeMerged" AS code,
                      "CompanyLithologyName" AS name, "GSWARockName" AS rock,
                      "GSWARegolithName" AS regolith, "Weathering_Code" AS weathering,
                      left("Comments", 160) AS comments
               FROM ref_geology WHERE "CollarId"=%s AND "ToDepth" > "FromDepth"
               ORDER BY "FromDepth" """,
            (int(record["wamex_collar_id"]),),
        )
    virtual, virtual_skill = [], []
    vrun = active_run("virtual_assay", "combined")
    if vrun:
        table = _parquet(vrun["run_id"], vrun["product_path"], "predictions.parquet")
        part = table[table.hole_id == hole_id]
        if len(part):
            bins = part.bin.to_numpy()
            virtual = [
                {"f": float(b), "t": float(b) + 1.0, **{k: float(v) for k, v in row.items()}}
                for b, row in zip(
                    bins, part.drop(columns=["hole_id", "bin"]).to_dict("records"), strict=True
                )
            ]
        virtual_skill = [r for r in vrun["card"].get("skill", []) if r.get("predictable")]
    survey = None
    if record and record["wamex_collar_id"]:
        from hylogger.explorer.desurvey import tvd_stations

        stations = query(
            'SELECT "Depth" AS d, "Dip" AS dip, "Azimuth" AS az FROM ref_survey '
            'WHERE "CollarId"=%s AND "Depth" >= 0 AND "Dip" BETWEEN -90 AND 90 ORDER BY "Depth"',
            (int(record["wamex_collar_id"]),),
        )
        if stations and any(abs(r["dip"]) > 0 for r in stations):
            md, tvd = tvd_stations(
                np.array([r["d"] for r in stations], float),
                np.array([r["dip"] for r in stations], float),
                np.array([r["az"] if r["az"] is not None else np.nan for r in stations], float),
            )
            survey = {
                "md": md.round(2).tolist(),
                "tvd": tvd.round(2).tolist(),
                "tail": float(abs(np.sin(np.radians(abs(stations[-1]["dip"]))))),
            }
    return clean(
        {
            "hole_id": hole_id,
            "tracks": tracks,
            "virtual": virtual,
            "virtual_skill": virtual_skill,
            "survey": survey,
            "assays": chemistry,
            "analytes": [a for a in ANALYTES if any(a in row for row in chemistry)],
            "lithology": lithology,
            "raw": _raw_bins(hole_id),
            "tsa_versions": _tsa_versions(hole_id, raw_status),
            "notes": {
                "abundance": "Mean normalised TSG component weight per diagnostic reading; not mass %",
                "assays": "GSWA interim package (unverified); negatives (below detection) shown as half "
                "the limit; oxides from element ppm where no oxide was reported",
            },
        }
    )


def _tsa_files() -> tuple[tuple[str, float], ...]:
    files = sorted((DATA_DIR / "products" / "raw").glob("*/tsa_versions.parquet"))
    return tuple((str(f), f.stat().st_mtime) for f in files)


@lru_cache(maxsize=2)
def _tsa_overview_cached(files: tuple[tuple[str, float], ...]) -> dict:
    import json
    from collections import Counter

    def main_group(text: str) -> str | None:
        groups = json.loads(text) if text else {}
        return max(groups, key=groups.get) if groups else None

    frames = []
    for path, _ in files:
        table = pd.read_parquet(path)
        table["hole_id"] = Path(path).parent.name
        frames.append(table)
    if not frames:
        return {"products": {}, "holes": {}}
    table = pd.concat(frames, ignore_index=True)
    table["system_group"] = table.system_groups.map(main_group)
    table["user_group"] = table.user_groups.map(main_group)
    products, holes = {}, {}
    for product, part in table.groupby("product"):
        part = part.assign(is_changed=part.system_top != part.user_top)
        per_hole = part.groupby("hole_id").agg(
            agreement=("min1_agreement", "mean"),
            metres=("bin", "size"),
            changed=("is_changed", "sum"),
        )
        changed = part[part.system_top != part.user_top]
        groups_known = part.system_group.notna() & part.user_group.notna()
        group_changed = part[groups_known & (part.system_group != part.user_group)]
        to_aspectral = changed.user_top.str.lower().isin(["aspectral", "notinlibrary", "higherror"])
        bands = [0, 0.7, 0.9, 0.97, 1.0001]
        counts = pd.cut(per_hole.agreement, bands, right=False).value_counts().sort_index()
        products[product] = {
            "holes": int(per_hole.shape[0]),
            "metres": int(len(part)),
            "readings": int(part.n_readings.sum()),
            "first_mineral_agreement": round(float(part.min1_agreement.mean()), 4),
            "median_hole_agreement": round(float(per_hole.agreement.median()), 4),
            "mix_overlap": round(float(part.mix_overlap.mean()), 4),
            "main_mineral_changed": round(len(changed) / len(part), 4),
            "main_group_changed": round(len(group_changed) / max(int(groups_known.sum()), 1), 4),
            "changed_to_no_match": round(float(to_aspectral.mean()), 4) if len(changed) else 0.0,
            "identical_holes": int((per_hole.agreement >= 0.999).sum()),
            "holes_by_agreement": [
                [bands[i], min(bands[i + 1], 1.0), int(n)] for i, n in enumerate(counts.to_numpy())
            ],
            "top_changes": [
                [a, b, int(n)]
                for (a, b), n in Counter(
                    zip(changed.system_top, changed.user_top, strict=True)
                ).most_common(10)
            ],
            "lowest": [
                [hole, round(float(r.agreement), 4), int(r.metres)]
                for hole, r in per_hole.sort_values("agreement").head(8).iterrows()
            ],
        }
        for hole, r in per_hole.iterrows():
            holes.setdefault(hole, {})[product] = round(float(r.agreement), 4)
    return {"products": products, "holes": holes}


def _tsa_overview() -> dict:
    return _tsa_overview_cached(_tsa_files())


@app.get("/api/v2/tsa-versions")
def tsa_versions_overview():
    """How often the reviewed (user) TSA run differs from the automatic (system) run, all holes."""
    overview = _tsa_overview()
    return clean(
        {
            "products": overview["products"],
            "meaning": "Share of readings whose first mineral is the same in the automatic (system) "
            "and reviewed (user) TSA runs, from raw packages summarised in this data root. The "
            "reviewed run is the one NVCL publishes; a difference is where an analyst re-ran or "
            "edited the automatic answer, not necessarily an automatic error.",
        }
    )


def _tsa_table(hole_id: str) -> pd.DataFrame | None:
    row = one(
        "SELECT product_path FROM raw_product WHERE hole_id=%s ORDER BY sensor LIMIT 1", (hole_id,)
    )
    if row is None:
        return None
    path = resolve(row["product_path"]) / "tsa_versions.parquet"
    return pd.read_parquet(path) if path.exists() else None


def _tsa_versions(hole_id: str, raw_status: str | None) -> dict:
    """System (automatic) vs user (reviewed) TSA per metre, or why it is missing."""
    import json

    from hylogger.explorer import tsa_versions

    table = _tsa_table(hole_id)
    if table is None:
        status = "needs_download" if str(raw_status).startswith("summarised") else "no_raw"
        return {"status": status}
    rows = [
        {
            "product": r.product,
            "f": r.depth_from_m,
            "t": r.depth_to_m,
            "system_top": r.system_top,
            "user_top": r.user_top,
            "min1_agreement": r.min1_agreement,
            "mix_overlap": r.mix_overlap,
            "system_groups": json.loads(r.system_groups),
            "user_groups": json.loads(r.user_groups),
            "system_minerals": json.loads(r.system_minerals),
            "user_minerals": json.loads(r.user_minerals),
        }
        for r in table.itertuples()
    ]
    return {"status": "available", "rows": rows, "summary": tsa_versions.summary(table)}


def _mean_mix(dicts: list[dict], top: int = 10) -> dict[str, float]:
    total: dict[str, float] = {}
    for item in dicts:
        for key, value in (item or {}).items():
            total[key] = total.get(key, 0.0) + float(value)
    n = max(len(dicts), 1)
    ranked = sorted(total.items(), key=lambda kv: -kv[1])[:top]
    return {k: round(v / n, 4) for k, v in ranked}


@app.get("/api/v2/holes/{hole_id}/range")
def depth_range(
    hole_id: str,
    start: float = Query(..., alias="from", ge=0),
    end: float = Query(..., alias="to", gt=0),
):
    """Everything known about one depth range: minerals, clusters, assays, geology, spectra."""
    import json

    if end <= start:
        raise HTTPException(422, "'to' must be deeper than 'from'")
    record = one(
        "SELECT wamex_collar_id, raw_status FROM explorer_hole WHERE hole_id=%s", (hole_id,)
    )
    if record is None:
        raise HTTPException(404, "unknown hole")
    sensors = {}
    for sensor in SENSORS:
        run = active_run("interval", sensor)
        rows = query(
            """SELECT f.minerals, f.groups, f.n_admitted, l.cluster_id, l.anomaly_reasons
               FROM interval_feature f JOIN active_feature_set a USING (feature_set_id)
               LEFT JOIN interval_label l ON l.run_id=%s AND l.feature_set_id=f.feature_set_id
                    AND l.bin=f.bin
               WHERE f.hole_id=%s AND f.sensor=%s AND f.depth_to_m > %s AND f.depth_from_m < %s""",
            (run["run_id"] if run else None, hole_id, sensor, start, end),
        )
        if not rows:
            continue
        diagnostic = [r for r in rows if r["n_admitted"]]
        names = _cluster_names(run["run_id"]) if run else {}
        clusters: dict[int, int] = {}
        reasons: dict[str, int] = {}
        for r in rows:
            if r["cluster_id"] is not None:
                clusters[r["cluster_id"]] = clusters.get(r["cluster_id"], 0) + 1
            for reason in r["anomaly_reasons"] or []:
                key = reason.split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
        sensors[sensor] = {
            "metres": len(rows),
            "diagnostic_metres": len(diagnostic),
            "minerals": _mean_mix([r["minerals"] for r in diagnostic]),
            "groups": _mean_mix([r["groups"] for r in diagnostic]),
            "clusters": [
                [cid, names.get(cid), round(n / len(rows), 4)]
                for cid, n in sorted(clusters.items(), key=lambda kv: -kv[1])[:6]
            ],
            "unusual_metres": sum(1 for r in rows if r["anomaly_reasons"]),
            "reasons": reasons,
        }
    chemistry, lithology = [], []
    if record["wamex_collar_id"]:
        with pool.connection() as conn:
            chem = assays(conn, [int(record["wamex_collar_id"])])
        chem = chem[(chem.t > start) & (chem.f < end)]
        for analyte in ANALYTES:
            if analyte in chem.columns and chem[analyte].notna().any():
                values = chem[analyte].dropna()
                chemistry.append(
                    {
                        "analyte": analyte,
                        "n": int(len(values)),
                        "median": float(values.median()),
                        "max": float(values.max()),
                        "unit": "%" if analyte in OXIDES else "ppm",
                    }
                )
        for row in query(
            """SELECT greatest("FromDepth", %s) AS f, least("ToDepth", %s) AS t,
                      coalesce("GSWARockName", "CompanyLithologyName",
                               "CompanyLithologyCodeMerged") AS rock
               FROM ref_geology WHERE "CollarId"=%s AND "ToDepth" > %s AND "FromDepth" < %s
               ORDER BY "FromDepth" """,
            (start, end, int(record["wamex_collar_id"]), start, end),
        ):
            lithology.append(row)
    spectra_items = {}
    for sensor in ("nir", "tir"):
        if one("SELECT 1 FROM raw_product WHERE hole_id=%s AND sensor=%s", (hole_id, sensor)):
            spectra_items[sensor] = spectra(holes=f"{hole_id}@{start}-{end}", sensor=sensor)
    versions = None
    table = _tsa_table(hole_id)
    if table is not None:
        part = table[(table.depth_to_m > start) & (table.depth_from_m < end)]
        versions = {}
        for product, rows in part.groupby("product"):
            versions[product] = {
                "metres": int(len(rows)),
                "min1_agreement": round(float(rows.min1_agreement.mean()), 4),
                "mix_overlap": round(float(rows.mix_overlap.mean()), 4),
                "system": _mean_mix([json.loads(v) for v in rows.system_minerals], 6),
                "user": _mean_mix([json.loads(v) for v in rows.user_minerals], 6),
                "changed_metres": int((rows.system_top != rows.user_top).sum()),
            }
    return clean(
        {
            "hole_id": hole_id,
            "from": start,
            "to": end,
            "sensors": sensors,
            "assays": chemistry,
            "lithology": lithology,
            "spectra": spectra_items,
            "tsa_versions": versions,
        }
    )


@app.get("/api/v2/holes/{hole_id}/export.csv")
def export_hole(hole_id: str):
    """All active intervals of one hole with minerals, labels and run identifiers."""
    import csv
    import io
    import json

    from fastapi.responses import Response

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "hole_id",
            "sensor",
            "depth_from_m",
            "depth_to_m",
            "n_total",
            "n_admitted",
            "dominant_mineral",
            "dominant_group",
            "cluster_id",
            "coarse_cluster_id",
            "domain_id",
            "anomaly_score",
            "anomaly_reasons",
            "minerals",
            "groups",
            "feature_set_id",
            "run_id",
        ]
    )
    for sensor in SENSORS:
        run = active_run("interval", sensor)
        rows = query(
            """SELECT f.*, l.cluster_id, l.coarse_cluster_id, l.domain_id, l.anomaly_score,
                      l.anomaly_reasons
               FROM interval_feature f JOIN active_feature_set a USING (feature_set_id)
               LEFT JOIN interval_label l ON l.run_id=%s AND l.feature_set_id=f.feature_set_id
                    AND l.bin=f.bin
               WHERE f.hole_id=%s AND f.sensor=%s ORDER BY f.bin""",
            (run["run_id"] if run else None, hole_id, sensor),
        )
        for r in rows:
            writer.writerow(
                [
                    hole_id,
                    sensor,
                    r["depth_from_m"],
                    r["depth_to_m"],
                    r["n_total"],
                    r["n_admitted"],
                    r["dominant_mineral"],
                    r["dominant_group"],
                    r["cluster_id"],
                    r["coarse_cluster_id"],
                    r["domain_id"],
                    r["anomaly_score"],
                    ";".join(r["anomaly_reasons"] or []),
                    json.dumps(r["minerals"]),
                    json.dumps(r["groups"]),
                    r["feature_set_id"],
                    run["run_id"] if run else "",
                ]
            )
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{hole_id}_explorer.csv"'},
    )


@app.get("/api/v2/intervals/{feature_set_id}/{bin_id}")
def interval_detail(feature_set_id: str, bin_id: int, limit: int = Query(12, le=50)):
    row = one(
        "SELECT * FROM interval_feature WHERE feature_set_id=%s AND bin=%s",
        (feature_set_id, bin_id),
    )
    if row is None:
        raise HTTPException(404, "unknown interval")
    run = active_run("interval", row["sensor"])
    label = None
    similar = []
    if run:
        label = one(
            "SELECT cluster_id, coarse_cluster_id, distance, domain_id, anomaly_score, anomaly_reasons "
            "FROM interval_label WHERE run_id=%s AND feature_set_id=%s AND bin=%s",
            (run["run_id"], feature_set_id, bin_id),
        )
        if label is not None:
            candidates = query(
                """SELECT f.feature_set_id, f.bin, f.hole_id, f.depth_from_m, f.depth_to_m, f.minerals
                   FROM interval_label l JOIN interval_feature f
                        ON f.feature_set_id=l.feature_set_id AND f.bin=l.bin
                   WHERE l.run_id=%s AND l.cluster_id=%s AND f.hole_id<>%s
                   ORDER BY random() LIMIT 20000""",
                (run["run_id"], label["cluster_id"], row["hole_id"]),
            )
            if candidates:
                minerals = sorted(
                    {m for c in candidates for m in c["minerals"]} | set(row["minerals"])
                )
                index = {m: i for i, m in enumerate(minerals)}
                matrix = np.zeros((len(candidates), len(minerals)))
                for i, c in enumerate(candidates):
                    for m, v in c["minerals"].items():
                        matrix[i, index[m]] = v
                target = np.zeros(len(minerals))
                for m, v in row["minerals"].items():
                    target[index[m]] = v
                distance = np.sqrt(((np.sqrt(matrix) - np.sqrt(target)) ** 2).sum(axis=1) / 2)
                for i in np.argsort(distance)[:limit]:
                    c = candidates[i]
                    similar.append(
                        {
                            "feature_set_id": c["feature_set_id"],
                            "bin": c["bin"],
                            "hole_id": c["hole_id"],
                            "depth_from_m": c["depth_from_m"],
                            "depth_to_m": c["depth_to_m"],
                            "distance": round(float(distance[i]), 4),
                        }
                    )
    spectrum = None
    raw = one(
        "SELECT product_path FROM raw_product WHERE hole_id=%s AND sensor=%s",
        (row["hole_id"], "nir" if row["sensor"] in ("swir", "vnir") else "tir"),
    )
    if raw:
        path = resolve(raw["product_path"])
        sensor = "nir" if row["sensor"] in ("swir", "vnir") else "tir"
        archive = np.load(path / f"raw_{sensor}.npz")
        match = np.flatnonzero(archive["bins"] == bin_id)
        if len(match):
            spectrum = {
                "wavelengths": archive["wavelengths"].round(1).tolist(),
                "values": archive["spectra"][match[0]].astype(float).round(4).tolist(),
                "sensor": sensor,
            }
    return clean(
        {
            "interval": row,
            "label": label,
            "similar": similar,
            "spectrum": spectrum,
            "similar_method": "Hellinger distance on mineral abundance, among intervals in the "
            "same fine cluster in other holes (random 20,000 candidates)",
        }
    )


@lru_cache(maxsize=64)
def _raw_spectra(product_path: str, sensor: str) -> dict:
    folder = resolve(product_path)
    archive = np.load(folder / f"raw_{sensor}.npz")
    table = pd.read_parquet(folder / f"raw_{sensor}.parquet").set_index("bin")
    bins = archive["bins"]
    depth = table.reindex(bins)
    return {
        "wavelengths": archive["wavelengths"].astype(float),
        "spectra": archive["spectra"].astype(np.float32),
        "from": depth["depth_from_m"].to_numpy(float),
        "to": depth["depth_to_m"].to_numpy(float),
        "features": depth[
            [c for c in table.columns if c.endswith(("_pos", "_depth")) or c == "albedo"]
        ],
    }


RAW_FEATURES = {
    "aloh_2200_pos": "Al-OH position (nm): white mica / kaolin chemistry",
    "feoh_2250_pos": "Fe-OH position (nm): chlorite / epidote",
    "mgoh_2330_pos": "Mg-OH / carbonate position (nm)",
    "h2o_1900_depth": "Water absorption depth at 1900 nm",
    "fe3_900_depth": "Ferric iron absorption depth at ~900 nm",
    "albedo": "Overall brightness",
}


@app.get("/api/v2/spectra")
def spectra(
    holes: str = Query(..., description="Comma-separated HOLE or HOLE@from-to (metres)"),
    sensor: str = Query("nir", pattern="^(nir|tir)$"),
):
    """Mean raw 1 m spectrum per hole or depth range, its 10-90% spread, and pairwise similarity."""
    items = []
    for token in [t.strip() for t in holes.split(",") if t.strip()][:8]:
        hole_id, _, span = token.partition("@")
        low = high = None
        if span:
            try:
                low, high = (float(v) for v in span.split("-", 1))
            except ValueError as error:
                raise HTTPException(
                    422, f"Bad depth range in {token!r}; use HOLE@from-to"
                ) from error
        item = {"hole_id": hole_id, "from_m": low, "to_m": high}
        row = one(
            "SELECT product_path FROM raw_product WHERE hole_id=%s AND sensor=%s", (hole_id, sensor)
        )
        if row is None:
            items.append({**item, "missing": f"No raw {sensor.upper()} summary for this hole"})
            continue
        raw = _raw_spectra(row["product_path"], sensor)
        keep = np.isfinite(raw["from"])
        if low is not None:
            keep &= (raw["to"] > low) & (raw["from"] < high)
        block = raw["spectra"][keep]
        if not len(block):
            items.append({**item, "missing": "No raw metres in this depth range"})
            continue
        p10, p90 = np.nanpercentile(block, [10, 90], axis=0)
        features = raw["features"][keep].median().dropna()
        items.append(
            {
                **item,
                "metres": int(keep.sum()),
                "depth": [float(np.nanmin(raw["from"][keep])), float(np.nanmax(raw["to"][keep]))],
                "mean": np.nanmean(block, axis=0).round(4).tolist(),
                "p10": p10.round(4).tolist(),
                "p90": p90.round(4).tolist(),
                "features": {k: round(float(v), 4) for k, v in features.items()},
                "_wavelengths": raw["wavelengths"],
            }
        )
    wavelengths = next((i.pop("_wavelengths") for i in items if "_wavelengths" in i), None)
    for item in items:
        item.pop("_wavelengths", None)
    means = [np.asarray(i["mean"]) if "mean" in i else None for i in items]
    angle = [
        [
            None
            if a is None or b is None
            else round(
                float(
                    np.degrees(
                        np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))
                    )
                ),
                2,
            )
            for b in means
        ]
        for a in means
    ]
    return clean(
        {
            "sensor": sensor,
            "wavelengths": None if wavelengths is None else wavelengths.round(1).tolist(),
            "items": items,
            "spectral_angle_deg": angle,
            "features_meaning": RAW_FEATURES,
            "method": "Median spectrum of each 1 m interval (Final Mask applied), averaged over the "
            "hole or depth range; shaded band = 10th-90th percentile of metres. Spectral angle "
            "compares shape and ignores brightness: below ~3° the spectra are nearly identical, "
            "above ~10° clearly different.",
        }
    )


# ---------------------------------------------------------- similarity etc.
@app.get("/api/v2/similarity")
def similarity(
    holes: str,
    view: str = Query(
        "combined", pattern="^(combined|swir|tir|raw_nir|raw_tir|seq_combined|seq_swir|seq_tir)$"
    ),
):
    run = active_run("hole", view)
    if run is None:
        raise HTTPException(404, f"No active {view} hole analysis")
    names, matrix, _ = _distance(run["run_id"], run["product_path"])
    wanted = [h.strip() for h in holes.split(",") if h.strip()]
    present = [h for h in dict.fromkeys(wanted) if h in names]
    if len(present) < 2:
        raise HTTPException(422, "Need at least two holes with this analysis")
    index = [names.index(h) for h in present]
    sub = matrix[np.ix_(index, index)].astype(float)
    order = list(range(len(present)))
    if len(present) > 2:
        from scipy.cluster.hierarchy import leaves_list, linkage
        from scipy.spatial.distance import squareform

        order = leaves_list(linkage(squareform(sub, checks=False), method="average")).tolist()
    ordered = [present[i] for i in order]
    profiles = {
        r["hole_id"]: r
        for r in query(
            "SELECT hole_id, cluster_id, summary FROM hole_profile WHERE run_id=%s AND hole_id=ANY(%s)",
            (run["run_id"], ordered),
        )
    }
    return clean(
        {
            "view": view,
            "run_id": run["run_id"],
            "distance": run["card"].get("distance"),
            "holes": ordered,
            "missing": [h for h in wanted if h not in names],
            "matrix": sub[np.ix_(order, order)].round(4).tolist(),
            "profiles": profiles,
        }
    )


@lru_cache(maxsize=16)
def _tree(run_id: str, path: str):
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform

    names, matrix, _ = _distance(run_id, path)
    return names, linkage(squareform(matrix.astype(float), checks=False), method="average")


@lru_cache(maxsize=16)
def _mineral_means(run_id: str, path: str, card_json: str) -> pd.DataFrame | None:
    import json

    card = json.loads(card_json)
    paths = []
    if card.get("views"):
        for view_run in card["views"].values():
            row = one("SELECT product_path FROM analysis_run WHERE run_id=%s", (view_run,))
            if row:
                paths.append(row["product_path"])
    else:
        paths.append(path)
    frames = []
    for product in paths:
        file = resolve(product) / "fingerprints.parquet"
        if file.exists():
            table = pd.read_parquet(file)
            frames.append(table[[c for c in table.columns if c.startswith(("mineral_", "class_"))]])
    if not frames:
        return None
    table = pd.concat(frames, axis=1).T.groupby(level=0).sum().T
    return table.div(table.sum(axis=1).replace(0, 1), axis=0)


@app.get("/api/v2/hole-clusters")
def hole_clusters(
    view: str = Query(
        "combined", pattern="^(combined|swir|tir|raw_nir|raw_tir|seq_combined|seq_swir|seq_tir)$"
    ),
    k: int = Query(..., ge=2, le=40),
):
    """Re-cut the stored average-linkage tree at a chosen number of hole clusters."""
    import json

    from scipy.cluster.hierarchy import fcluster

    from hylogger.explorer.clusters import name_profile

    run = active_run("hole", view)
    if run is None:
        raise HTTPException(404, f"No active {view} hole analysis")
    names, tree = _tree(run["run_id"], run["product_path"])
    labels = fcluster(tree, min(k, len(names)), criterion="maxclust") - 1
    order = np.argsort(-np.bincount(labels))
    remap = np.empty(len(order), dtype=int)
    remap[order] = np.arange(len(order))
    labels = remap[labels]
    means = _mineral_means(run["run_id"], run["product_path"], json.dumps(run["card"], default=str))
    cluster_names, sizes = {}, {}
    for cluster in np.unique(labels):
        members = [names[i] for i in np.flatnonzero(labels == cluster)]
        sizes[int(cluster)] = len(members)
        if means is not None:
            mix = means.reindex(members).mean()
            mix.index = [
                str(c).removeprefix("mineral_").replace("class_", "class ") for c in mix.index
            ]
            cluster_names[int(cluster)] = name_profile(mix, dominant_share=0.5, parts=3)
    return clean(
        {
            "view": view,
            "run_id": run["run_id"],
            "k": int(len(np.unique(labels))),
            "assignments": dict(zip(names, labels.tolist(), strict=True)),
            "names": cluster_names,
            "sizes": sizes,
        }
    )


@lru_cache(maxsize=16)
def _cluster_names(run_id: str | None) -> dict[int, str]:
    if not run_id:
        return {}
    return {
        r["cluster_id"]: r["name"]
        for r in query(
            "SELECT cluster_id, name FROM cluster_profile WHERE run_id=%s AND level='fine'",
            (run_id,),
        )
    }


def _fingerprints(view: str) -> tuple[dict, pd.DataFrame] | None:
    run = active_run("hole", view)
    if run is None or not (resolve(run["product_path"]) / "fingerprints.parquet").exists():
        return None
    return run, _parquet(run["run_id"], run["product_path"], "fingerprints.parquet")


def _shares(table: pd.DataFrame, hole_id: str, prefix: str, names: dict | None = None) -> dict:
    columns = [c for c in table.columns if c.startswith(prefix)]
    values = table.loc[hole_id, columns].astype(float)
    total = float(values.sum()) or 1.0
    out: dict[str, float] = {}
    for column, value in values.items():
        if value > 0:
            key = column.removeprefix(prefix)
            label = names.get(int(key), f"cluster {key}") if names is not None else key
            out[label] = out.get(label, 0.0) + float(value) / total
    return out


MIX_SENSORS = {
    "combined": ("swir", "tir"),
    "swir": ("swir",),
    "tir": ("tir",),
    "seq_combined": ("swir", "tir"),
    "seq_swir": ("swir",),
    "seq_tir": ("tir",),
}


def _explain(view: str, hole_id: str, items: list[dict], district: int | None) -> None:
    """Attach plain-language reasons (`why`) and their numbers (`explain`) to each neighbour."""
    from hylogger.explorer import explain

    for item in items:
        item["why"], item["explain"] = [], {}
    for sensor in MIX_SENSORS.get(view, ()):
        found = _fingerprints(sensor)
        if found is None or hole_id not in found[1].index:
            continue
        run, table = found
        names = _cluster_names(run["card"].get("interval_run"))
        mine_c = _shares(table, hole_id, "cluster_", names)
        mine_m = _shares(table, hole_id, "mineral_")
        for item in items:
            if item["hole_id"] not in table.index:
                continue
            clusters = explain.overlap(mine_c, _shares(table, item["hole_id"], "cluster_", names))
            minerals = explain.overlap(mine_m, _shares(table, item["hole_id"], "mineral_"))
            item["why"] += explain.mix_lines(sensor.upper(), clusters, minerals)
            item["explain"][sensor] = {"clusters": clusters, "minerals": minerals}
    if view.startswith("seq_"):
        sensor = "tir" if view == "seq_tir" else "swir"
        for item in items:
            try:
                zones = _align(hole_id, item["hole_id"], sensor)["zones"]
            except HTTPException:
                continue
            summary = explain.sequence_summary(zones)
            item["explain"]["sequence"] = summary
            line = explain.sequence_line(summary)
            if line:
                item["why"].insert(0, line)
    if view.startswith("raw_"):
        sensor = view.removeprefix("raw_")
        found = _fingerprints(view)
        bands = {
            r["hole_id"]: (r["summary"] or {}).get("feature_medians") or {}
            for r in query(
                "SELECT hole_id, summary FROM raw_product WHERE sensor=%s AND hole_id=ANY(%s)",
                (sensor, [hole_id] + [i["hole_id"] for i in items]),
            )
        }
        for item in items:
            classes = None
            if found and hole_id in found[1].index and item["hole_id"] in found[1].index:
                classes = explain.overlap(
                    _shares(found[1], hole_id, "class_"),
                    _shares(found[1], item["hole_id"], "class_"),
                )
                item["explain"]["raw"] = classes
            item["why"] += explain.raw_lines(
                sensor.upper(), classes, bands.get(hole_id, {}), bands.get(item["hole_id"], {})
            )
    for item in items:
        same = district is not None and item.get("district_id") == district
        line = explain.context_line(item.get("km"), same)
        if line:
            item["why"].append(line)


@app.get("/api/v2/holes/{hole_id}/similar")
def similar_holes(
    hole_id: str,
    view: str = Query(
        "combined", pattern="^(combined|swir|tir|raw_nir|raw_tir|seq_combined|seq_swir|seq_tir)$"
    ),
    limit: int = Query(15, le=50),
):
    run = active_run("hole", view)
    if run is None:
        return {"view": view, "items": []}
    rows = query(
        """SELECT n.neighbor_id AS hole_id, n.distance, n.rank, h.commodity, h.tectonic_region,
                  h.lat, h.lon, h.district_id, p.cluster_id, p.summary,
                  ST_Distance(h.geom, s.geom) / 1000 AS km
           FROM hole_neighbor n JOIN explorer_hole h ON h.hole_id=n.neighbor_id
           JOIN explorer_hole s ON s.hole_id=n.hole_id
           LEFT JOIN hole_profile p ON p.run_id=n.run_id AND p.hole_id=n.neighbor_id
           WHERE n.run_id=%s AND n.hole_id=%s ORDER BY n.rank LIMIT %s""",
        (run["run_id"], hole_id, limit),
    )
    me = one("SELECT district_id FROM explorer_hole WHERE hole_id=%s", (hole_id,))
    _explain(view, hole_id, rows, (me or {}).get("district_id"))
    return clean(
        {
            "view": view,
            "run_id": run["run_id"],
            "distance": run["card"].get("distance"),
            "items": rows,
        }
    )


def _align(a: str, b: str, sensor: str) -> dict:
    from hylogger.explorer import sequence

    run = active_run("hole", f"seq_{sensor}")
    if run is None:
        raise HTTPException(404, f"No active {sensor} sequence analysis")
    steps = _parquet(run["run_id"], run["product_path"], "steps.parquet")
    columns = [c for c in steps.columns if c.startswith("c") and c[1:].isdigit()]
    parts = {}
    for hole_id in (a, b):
        part = steps[steps.hole_id == hole_id].sort_values("step").reset_index(drop=True)
        if len(part) < sequence.MIN_STEPS:
            raise HTTPException(422, f"{hole_id} has too few {sensor.upper()} steps to align")
        parts[hole_id] = part
    distance, path = sequence.dtw_path(
        parts[a][columns].to_numpy(np.float32), parts[b][columns].to_numpy(np.float32)
    )
    if not path:
        raise HTTPException(422, "These holes cannot be aligned within the warping band")
    zones = sequence.matched_zones(path, parts[a], parts[b])
    names = _cluster_names(run["card"].get("interval_run"))
    for zone in zones:
        zone["cluster_name"] = names.get(zone["cluster_id"])
    return {
        "run": run,
        "parts": parts,
        "distance": distance,
        "path": path,
        "zones": zones,
        "names": names,
    }


@app.get("/api/v2/align")
def align(a: str, b: str, sensor: str = Query("swir", pattern="^(swir|tir)$")):
    """DTW alignment of two holes' down-hole sequences: matched steps and zones."""
    from hylogger.explorer import explain

    result = _align(a, b, sensor)

    def describe(part):
        return [
            {"f": r.md_from_m, "t": r.md_to_m, "tvd": r.tvd_from_m, "cluster_id": int(r.cluster_id)}
            for r in part.itertuples()
        ]

    summary = explain.sequence_summary(result["zones"])
    return clean(
        {
            "sensor": sensor,
            "run_id": result["run"]["run_id"],
            "a": a,
            "b": b,
            "distance": round(result["distance"], 4),
            "steps_a": describe(result["parts"][a]),
            "steps_b": describe(result["parts"][b]),
            "path": result["path"],
            "zones": result["zones"],
            "cluster_names": result["names"],
            "summary": {**summary, "sentence": explain.sequence_line(summary)},
            "method": result["run"]["card"].get("distance"),
        }
    )


@app.get("/api/v2/embedding")
def embedding(
    view: str = Query(
        "combined", pattern="^(combined|swir|tir|raw_nir|raw_tir|seq_combined|seq_swir|seq_tir)$"
    ),
):
    run = active_run("hole", view)
    if run is None:
        return {"view": view, "points": []}
    points = query(
        """SELECT p.hole_id, p.x, p.y, p.cluster_id, p.anomaly_score, h.commodity,
                  h.tectonic_region, h.district_id
           FROM hole_profile p JOIN explorer_hole h USING (hole_id) WHERE p.run_id=%s""",
        (run["run_id"],),
    )
    return clean({"view": view, "run_id": run["run_id"], "card": run["card"], "points": points})


@lru_cache(maxsize=8)
def _cluster_where(run_id: str) -> dict:
    """Per cluster (fine and coarse): depth range of its metres and the holes holding most of it."""
    out: dict[str, dict] = {"fine": {}, "coarse": {}}
    for level, column in (("fine", "cluster_id"), ("coarse", "coarse_cluster_id")):
        for r in query(
            f"""SELECT l.{column} AS cid,
                       percentile_cont(ARRAY[0.1, 0.5, 0.9]) WITHIN GROUP (ORDER BY f.depth_from_m)
                           AS depth
                FROM interval_label l JOIN interval_feature f
                     ON f.feature_set_id=l.feature_set_id AND f.bin=l.bin
                WHERE l.run_id=%s AND l.{column} IS NOT NULL GROUP BY 1""",
            (run_id,),
        ):
            out[level][r["cid"]] = {
                "depth_p10_p50_p90": [round(v, 1) for v in r["depth"]],
                "holes": [],
            }
        for r in query(
            f"""SELECT cid, hole_id, n FROM (
                    SELECT l.{column} AS cid, l.hole_id, count(*) AS n,
                           row_number() OVER (PARTITION BY l.{column} ORDER BY count(*) DESC) AS r
                    FROM interval_label l WHERE l.run_id=%s AND l.{column} IS NOT NULL
                    GROUP BY 1, 2) x
                WHERE r <= 4 ORDER BY cid, n DESC""",
            (run_id,),
        ):
            out[level].setdefault(r["cid"], {"holes": []})["holes"].append([r["hole_id"], r["n"]])
    return out


@app.get("/api/v2/clusters")
def clusters(sensor: str = Query("swir", pattern="^(swir|tir)$")):
    run = active_run("interval", sensor)
    if run is None:
        raise HTTPException(404, f"No active {sensor} interval analysis")
    profiles = query(
        "SELECT level, cluster_id, parent_id, name, n_intervals, n_holes, minerals, chemistry "
        "FROM cluster_profile WHERE run_id=%s ORDER BY level DESC, cluster_id",
        (run["run_id"],),
    )
    card = dict(run["card"])
    for key in ("features", "minerals", "mineral_groups"):
        card.pop(key, None)
    raw = {}
    for view in ("raw_nir", "raw_tir"):
        raw_run = active_run("hole", view)
        if raw_run:
            raw[view] = {
                k: raw_run["card"].get(k)
                for k in (
                    "class_profiles",
                    "grid_nm",
                    "pca_components",
                    "pca_variance",
                    "interval_classes",
                    "agreement_with_processed",
                    "holes",
                    "intervals",
                    "hole_clustering",
                    "preprocessing",
                )
            }
    vrun = active_run("virtual_assay", "combined")
    virtual = (
        {k: vrun["card"].get(k) for k in ("skill", "method", "assay_intervals", "meaning")}
        if vrun
        else None
    )
    robust_run = active_run("robustness", sensor)
    robustness = robust_run["card"] if robust_run else None
    return clean(
        {
            "sensor": sensor,
            "run_id": run["run_id"],
            "card": card,
            "profiles": profiles,
            "where": _cluster_where(run["run_id"]),
            "raw": raw,
            "virtual": virtual,
            "robustness": robustness,
        }
    )


@app.get("/api/v2/anomalies")
def anomalies(
    sensor: str = Query("swir", pattern="^(swir|tir)$"),
    limit: int = Query(200, le=2000),
    commodity: str | None = None,
    region: str | None = None,
    reason: str | None = None,
):
    run = active_run("interval", sensor)
    if run is None:
        return {"sensor": sensor, "intervals": [], "holes": []}
    intervals = query(
        """SELECT l.feature_set_id, l.bin, l.hole_id, f.depth_from_m, f.depth_to_m, l.anomaly_score,
                  l.anomaly_reasons, l.cluster_id, f.dominant_mineral, h.commodity,
                  h.tectonic_region, h.lat, h.lon
           FROM interval_label l
           JOIN interval_feature f ON f.feature_set_id=l.feature_set_id AND f.bin=l.bin
           JOIN explorer_hole h ON h.hole_id=l.hole_id
           WHERE l.run_id=%s AND cardinality(l.anomaly_reasons) > 0
             AND (%s::text IS NULL OR h.commodity ILIKE '%%' || %s || '%%')
             AND (%s::text IS NULL OR h.tectonic_region = %s)
             AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM unnest(l.anomaly_reasons) r
                                              WHERE r LIKE %s || '%%'))
           ORDER BY l.anomaly_score DESC LIMIT %s""",
        (run["run_id"], commodity, commodity, region, region, reason, reason, limit),
    )
    hole_run = active_run("hole", "combined") or active_run("hole", sensor)
    holes = []
    if hole_run:
        holes = query(
            """SELECT p.hole_id, p.anomaly_score, p.anomaly_reasons, h.commodity, h.tectonic_region
               FROM hole_profile p JOIN explorer_hole h USING (hole_id)
               WHERE p.run_id=%s ORDER BY p.anomaly_score DESC LIMIT 30""",
            (hole_run["run_id"],),
        )
    return clean(
        {
            "sensor": sensor,
            "run_id": run["run_id"],
            "card": run["card"].get("anomaly"),
            "data_quality": run["card"].get("data_quality_outliers"),
            "intervals": intervals,
            "holes": holes,
        }
    )


REASON_TEXT = {
    "poor_fit": "Poor fit to its cluster",
    "isolation": "Rare combination of minerals",
    "downhole_contrast": "Unlike the metres around it",
    "rare_mineral": "Contains a rare mineral",
    "chemistry_mismatch": "Assay disagrees with the mineralogy",
    "unlike_other_holes": "Unlike every other hole",
}


@lru_cache(maxsize=256)
def _mineral_prevalence(sensor: str, mineral: str) -> float | None:
    row = one(
        """SELECT avg((f.minerals ? %s)::int) AS share
           FROM interval_feature f JOIN active_feature_set a USING (feature_set_id)
           WHERE f.sensor=%s AND f.n_admitted > 0""",
        (mineral, sensor),
    )
    return None if row is None or row["share"] is None else float(row["share"])


def _differences(here: dict, there: dict, n: int = 3) -> str:
    keys = set(here) | set(there)
    ranked = sorted(keys, key=lambda k: -abs(here.get(k, 0.0) - there.get(k, 0.0)))[:n]
    return ", ".join(
        f"{k} {here.get(k, 0.0):.0%} here vs {there.get(k, 0.0):.0%}"
        for k in ranked
        if abs(here.get(k, 0.0) - there.get(k, 0.0)) >= 0.05
    )


@app.get("/api/v2/anomalies/interval/{feature_set_id}/{bin_id}")
def anomaly_interval(feature_set_id: str, bin_id: int):
    """Why one metre is flagged: each reason with the numbers behind it, and its context."""
    row = one(
        "SELECT * FROM interval_feature WHERE feature_set_id=%s AND bin=%s",
        (feature_set_id, bin_id),
    )
    if row is None:
        raise HTTPException(404, "unknown interval")
    sensor, hole_id = row["sensor"], row["hole_id"]
    run = active_run("interval", sensor)
    if run is None:
        raise HTTPException(404, f"No active {sensor} interval analysis")
    label = one(
        "SELECT cluster_id, distance, anomaly_score, anomaly_reasons FROM interval_label "
        "WHERE run_id=%s AND feature_set_id=%s AND bin=%s",
        (run["run_id"], feature_set_id, bin_id),
    )
    if label is None:
        raise HTTPException(404, "interval not labelled in the active analysis")
    here = row["minerals"] or {}
    context = query(
        """SELECT f.bin, f.depth_from_m, f.depth_to_m, f.groups, f.minerals, f.dominant_mineral,
                  l.cluster_id, l.anomaly_score, l.anomaly_reasons
           FROM interval_feature f LEFT JOIN interval_label l ON l.run_id=%s
                AND l.feature_set_id=f.feature_set_id AND l.bin=f.bin
           WHERE f.feature_set_id=%s AND f.bin BETWEEN %s AND %s ORDER BY f.bin""",
        (run["run_id"], feature_set_id, bin_id - 10, bin_id + 10),
    )
    around = [
        c["minerals"] or {}
        for c in context
        if c["bin"] != bin_id and abs(c["bin"] - bin_id) <= 5 and c["minerals"]
    ]
    around_mix = _mean_mix(around, 12)
    profile = one(
        "SELECT name, minerals, n_intervals FROM cluster_profile WHERE run_id=%s "
        "AND level='fine' AND cluster_id=%s",
        (run["run_id"], label["cluster_id"]),
    )
    total = run["card"].get("intervals")
    evidence = []
    for reason in label["anomaly_reasons"] or []:
        key, _, detail = reason.partition(":")
        item = {"reason": key, "title": REASON_TEXT.get(key, key)}
        if key == "poor_fit" and profile:
            diff = _differences(here, profile["minerals"] or {})
            item["text"] = (
                f"It sits in cluster “{profile['name']}” but fits it less well than 98% of metres "
                f"fit their own cluster. {('Biggest differences from that cluster: ' + diff + '.') if diff else ''}"
            )
        elif key == "isolation":
            top = ", ".join(
                f"{m} {v:.0%}" for m, v in sorted(here.items(), key=lambda kv: -kv[1])[:4]
            )
            item["text"] = (
                f"Its combination ({top}) is easy to separate from the other "
                f"{total:,} metres: fewer random splits isolate it than for 98% of metres."
                if total
                else f"Its combination ({top}) is rare among all metres."
            )
        elif key == "downhole_contrast":
            diff = _differences(here, around_mix)
            item["text"] = (
                f"It differs sharply from the metres within 5 m above and below. "
                f"{('Mineral changes: ' + diff + ' around it.') if diff else ''}"
            )
        elif key == "rare_mineral":
            parts = []
            for mineral in detail.split("/"):
                prevalence = _mineral_prevalence(sensor, mineral)
                share = here.get(mineral, 0.0)
                parts.append(
                    f"{mineral} ({share:.0%} of this metre; found in "
                    f"{prevalence:.2%} of all metres)"
                    if prevalence is not None
                    else mineral
                )
            item["text"] = "Contains " + "; ".join(parts) + "."
        elif key == "chemistry_mismatch":
            item["text"] = _chemistry_evidence(hole_id, bin_id, row, detail)
        evidence.append(item)
    spectrum = None
    raw_sensor = "nir" if sensor in ("swir", "vnir") else "tir"
    raw = one(
        "SELECT product_path FROM raw_product WHERE hole_id=%s AND sensor=%s", (hole_id, raw_sensor)
    )
    if raw:
        data = _raw_spectra(raw["product_path"], raw_sensor)
        match = np.flatnonzero(np.isclose(data["from"], row["depth_from_m"]))
        if len(match):
            spectrum = {
                "sensor": raw_sensor,
                "wavelengths": data["wavelengths"].round(1).tolist(),
                "values": data["spectra"][match[0]].round(4).tolist(),
            }
    return clean(
        {
            "interval": {
                k: row[k]
                for k in ("hole_id", "sensor", "bin", "depth_from_m", "depth_to_m", "n_admitted")
            },
            "minerals": dict(sorted(here.items(), key=lambda kv: -kv[1])[:8]),
            "score": label["anomaly_score"],
            "cluster": {"id": label["cluster_id"], "name": (profile or {}).get("name")},
            "evidence": evidence,
            "context": context,
            "around": around_mix,
            "spectrum": spectrum,
            "meaning": "Unusualness is the average percentile of the checks across all metres of "
            "this sensor: 0.99 means more unusual than 99% of metres. A reason is listed when a "
            "single check alone is in the top 2%.",
        }
    )


def _chemistry_evidence(hole_id: str, bin_id: int, row: dict, analyte: str) -> str:
    vrun = active_run("virtual_assay", "combined")
    record = one("SELECT wamex_collar_id FROM explorer_hole WHERE hole_id=%s", (hole_id,))
    predicted = measured = None
    if vrun:
        table = _parquet(vrun["run_id"], vrun["product_path"], "predictions.parquet")
        hit = table[(table.hole_id == hole_id) & (table.bin == bin_id)]
        if len(hit) and analyte in hit.columns:
            predicted = float(hit[analyte].iloc[0])
    if record and record["wamex_collar_id"]:
        with pool.connection() as conn:
            chem = assays(conn, [int(record["wamex_collar_id"])])
        hit = chem[(chem.t > row["depth_from_m"]) & (chem.f < row["depth_to_m"])]
        if analyte in hit.columns and hit[analyte].notna().any():
            measured = float(hit[analyte].dropna().median())
    unit = "%" if analyte in OXIDES else " ppm"
    if measured is not None and predicted is not None:
        direction = "higher" if measured > predicted else "lower"
        return (
            f"The assay gives {analyte} {measured:.3g}{unit}, but from its minerals the model "
            f"expects about {predicted:.3g}{unit} — much {direction} than metres with this "
            "mineralogy usually have (top 2% of differences)."
        )
    return f"The {analyte} assay differs strongly from what the mineralogy predicts (top 2%)."


@lru_cache(maxsize=8)
def _nearest_distances(run_id: str, path: str) -> tuple[list[str], np.ndarray]:
    names, matrix, _ = _distance(run_id, path)
    values = matrix.astype(float).copy()
    np.fill_diagonal(values, np.inf)
    return names, np.sort(values, axis=1)[:, :5].mean(axis=1)


@app.get("/api/v2/anomalies/hole/{hole_id}")
def anomaly_hole(
    hole_id: str,
    view: str = Query(
        "combined", pattern="^(combined|swir|tir|raw_nir|raw_tir|seq_combined|seq_swir|seq_tir)$"
    ),
):
    """Why a whole hole stands out: distance to its nearest holes and its distinctive minerals."""
    run = active_run("hole", view)
    if run is None:
        raise HTTPException(404, f"No active {view} hole analysis")
    names, nearest = _nearest_distances(run["run_id"], run["product_path"])
    if hole_id not in names:
        raise HTTPException(404, f"{hole_id} is not in the {view} analysis")
    mine = float(nearest[names.index(hole_id)])
    neighbours = query(
        """SELECT n.neighbor_id AS hole_id, n.distance, ST_Distance(h.geom, s.geom)/1000 AS km
           FROM hole_neighbor n JOIN explorer_hole h ON h.hole_id=n.neighbor_id
           JOIN explorer_hole s ON s.hole_id=n.hole_id
           WHERE n.run_id=%s AND n.hole_id=%s ORDER BY n.rank LIMIT 5""",
        (run["run_id"], hole_id),
    )
    distinctive = []
    for sensor in MIX_SENSORS.get(view, ()):
        found = _fingerprints(sensor)
        if found is None or hole_id not in found[1].index:
            continue
        table = found[1][[c for c in found[1].columns if c.startswith("mineral_")]].astype(float)
        table = table.div(table.sum(axis=1).replace(0, 1), axis=0)
        own, mean = table.loc[hole_id], table.mean()
        for column in own.index:
            if own[column] >= 0.05 and own[column] >= 3 * mean[column]:
                distinctive.append(
                    {
                        "sensor": sensor.upper(),
                        "mineral": column.removeprefix("mineral_"),
                        "share": round(float(own[column]), 3),
                        "typical": round(float(mean[column]), 3),
                    }
                )
    distinctive.sort(key=lambda d: -d["share"] / max(d["typical"], 1e-3))
    return clean(
        {
            "hole_id": hole_id,
            "view": view,
            "percentile": round(float((nearest < mine).mean()), 4),
            "mean_distance_to_5_nearest": round(mine, 4),
            "typical_mean_distance": round(float(np.median(nearest)), 4),
            "neighbours": neighbours,
            "distinctive": distinctive[:6],
            "meaning": "Mean fingerprint distance to its five most similar holes, compared with "
            "every other hole. A high value means even its closest matches are not very close: "
            "a hole unlike the rest, or one with an unusual data quality.",
        }
    )


# --------------------------------------------------------------- prediction
@app.get("/api/v2/predict")
def predict(
    lat: float = Query(..., ge=-40, le=-5),
    lon: float = Query(..., ge=105, le=135),
    sensor: str = Query("swir", pattern="^(swir|tir)$"),
):
    run = active_run("prediction", sensor)
    if run is None:
        raise HTTPException(404, f"No active {sensor} prediction model")
    holes = _parquet(run["run_id"], run["product_path"], "holes.parquet")
    groups = run["card"]["groups"]
    model = _interpolator(run["run_id"], run["product_path"])
    mean, spread, dist, index = model.query(lat, lon)
    support = run["card"]["summary"].get("support_limit_km")
    expected = next(
        (row for row in run["card"]["by_distance"] if row["from_km"] <= dist[0] < row["to_km"]),
        None,
    )
    bands = _parquet(run["run_id"], run["product_path"], "bands.parquet")
    neighbour_ids = holes.index[index].tolist()
    band_predictions = []
    for band, (low, high) in enumerate(run["card"]["depth_bands_m"]):
        part = bands[(bands.band == band) & bands.hole_id.isin(neighbour_ids)]
        if len(part) < 2:
            continue
        weights = np.array([1 / max(dist[neighbour_ids.index(h)], 0.05) ** 2 for h in part.hole_id])
        weights /= weights.sum()
        values = part[groups].to_numpy()
        band_predictions.append(
            {
                "band": band,
                "from_m": low,
                "to_m": high,
                "holes": int(len(part)),
                "composition": dict(
                    zip(groups, (weights[:, None] * values).sum(axis=0).round(4), strict=True)
                ),
            }
        )
    order = np.argsort(-mean)
    weights = 1 / np.maximum(dist, 0.05) ** predict_lib.POWER
    weights = weights / weights.sum()

    def mix(values) -> dict:
        return {g: round(float(v), 4) for g, v in zip(groups, values, strict=True) if v > 0.005}

    neighbour_bands = {
        hole_id: {
            int(band): mix(values)
            for band, values in zip(part.band, part[groups].to_numpy(), strict=True)
        }
        for hole_id, part in bands[bands.hole_id.isin(neighbour_ids)].groupby("hole_id")
    }
    return clean(
        {
            "sensor": sensor,
            "run_id": run["run_id"],
            "location": {"lat": lat, "lon": lon},
            "composition": {groups[i]: round(float(mean[i]), 4) for i in order if mean[i] > 0.005},
            "spread": {groups[i]: round(float(spread[i]), 4) for i in order if mean[i] > 0.005},
            "neighbours": [
                {
                    "hole_id": neighbour_ids[j],
                    "km": round(float(dist[j]), 2),
                    "weight": round(float(weights[j]), 4),
                    "lat": float(holes.lat.iloc[index[j]]),
                    "lon": float(holes.lon.iloc[index[j]]),
                    "composition": mix(holes[groups].iloc[index[j]].to_numpy()),
                    "bands": neighbour_bands.get(neighbour_ids[j], {}),
                }
                for j in range(len(index))
            ],
            "nearest_km": round(float(dist[0]), 2),
            "support_limit_km": support,
            "within_support": support is not None and dist[0] <= support,
            "expected_error": expected,
            "depth_bands": band_predictions,
            "method": run["card"]["method"],
            "validation": run["card"]["summary"],
        }
    )


@lru_cache(maxsize=4)
def _interpolator(run_id: str, path: str) -> predict_lib.Interpolator:
    holes = _parquet(run_id, path, "holes.parquet")
    groups = [c for c in holes.columns if c not in ("lat", "lon")]
    return predict_lib.Interpolator(
        holes.lat.to_numpy(), holes.lon.to_numpy(), holes[groups].to_numpy(), holes.index.tolist()
    )


@app.get("/api/v2/prediction/cv")
def prediction_cv(sensor: str = Query("swir", pattern="^(swir|tir)$")):
    run = active_run("prediction", sensor)
    if run is None:
        raise HTTPException(404, "No active prediction model")
    cv = _parquet(run["run_id"], run["product_path"], "cv.parquet")
    holes = _parquet(run["run_id"], run["product_path"], "holes.parquet")
    cv = cv.merge(holes[["lat", "lon"]], left_on="hole_id", right_index=True, how="left")
    return clean(
        {
            "sensor": sensor,
            "run_id": run["run_id"],
            "card": run["card"],
            "holes": cv.round(4).to_dict("records"),
        }
    )


# -------------------------------------------------------------- data & jobs
@lru_cache(maxsize=12)
def _analyte(analyte: str) -> pd.DataFrame:
    from hylogger.explorer.chemistry import analyte_values

    with pool.connection() as conn:
        return analyte_values(conn, analyte)


@app.get("/api/v2/chemistry")
def chemistry_search(analyte: str = Query(..., max_length=8), threshold: float = Query(0, ge=0)):
    """Per hole: assay coverage, maximum and samples above a threshold for one analyte."""
    if analyte not in ANALYTES:
        raise HTTPException(422, f"Unknown analyte; choose one of {', '.join(ANALYTES)}")
    values = _analyte(analyte)
    rejected = values[values.rejected].groupby("collar_id").size()
    values = values[~values.rejected]
    if values.empty:
        return {"analyte": analyte, "threshold": threshold, "unit": None, "holes": []}
    grouped = values.groupby("collar_id")
    stats = pd.DataFrame(
        {
            "n": grouped.size(),
            "median": grouped.value.median(),
            "max": grouped.value.max(),
            "above": grouped.value.apply(lambda s: int((s > threshold).sum())),
        }
    )
    top = values.loc[grouped.value.idxmax(), ["collar_id", "f", "t"]].set_index("collar_id")
    stats = stats.join(top)
    stats["implausible_removed"] = rejected.reindex(stats.index).fillna(0).astype(int)
    holes = pd.DataFrame(
        query(
            "SELECT hole_id, wamex_collar_id FROM explorer_hole WHERE wamex_collar_id IS NOT NULL"
        )
    )
    merged = holes.merge(stats, left_on="wamex_collar_id", right_index=True)
    merged = merged.sort_values("max", ascending=False)
    unit = "wt%" if analyte in OXIDES or analyte == "LOI" else "ppm"
    return clean(
        {
            "analyte": analyte,
            "threshold": threshold,
            "unit": unit,
            "holes_with_assays": int(len(merged)),
            "holes_above": int((merged.above > 0).sum()),
            "holes": merged.drop(columns="wamex_collar_id").round(4).to_dict("records"),
            "note": "GSWA interim package assays (unverified); reporting codes (<= -1000) and "
            "physically implausible values removed; below-detection values at half the limit",
        }
    )


@app.get("/api/v2/catalog")
def catalog(
    search: str | None = None,
    commodity: str | None = None,
    region: str | None = None,
    status: str | None = None,
    raw: bool | None = None,
    assays_only: bool = False,
    limit: int = Query(500, le=3000),
):
    rows = query(
        """SELECT hole_id, name, commodity, tectonic_region, operator, project, length_m,
                  nvcl_available, raw_available, raw_size_bytes, n_assay, n_geology,
                  processed_status, raw_status, lat, lon
           FROM explorer_hole
           WHERE (%s::text IS NULL OR hole_id ILIKE '%%'||%s||'%%' OR name ILIKE '%%'||%s||'%%'
                  OR project ILIKE '%%'||%s||'%%' OR operator ILIKE '%%'||%s||'%%')
             AND (%s::text IS NULL OR commodity ILIKE '%%'||%s||'%%')
             AND (%s::text IS NULL OR tectonic_region = %s)
             AND (%s::text IS NULL OR processed_status = %s OR raw_status = %s)
             AND (%s::boolean IS NULL OR raw_available = %s)
             AND (NOT %s OR n_assay > 0)
           ORDER BY hole_id LIMIT %s""",
        (
            search,
            search,
            search,
            search,
            search,
            commodity,
            commodity,
            region,
            region,
            status,
            status,
            status,
            raw,
            raw,
            assays_only,
            limit,
        ),
    )
    regions = [
        r["tectonic_region"]
        for r in query(
            "SELECT DISTINCT tectonic_region FROM explorer_hole WHERE tectonic_region IS NOT NULL ORDER BY 1"
        )
    ]
    return clean({"rows": rows, "regions": regions})


@app.post("/api/v2/estimate")
def estimate(payload: Annotated[dict, Body()]):
    holes = list(dict.fromkeys(payload.get("holes", [])))[:MAX_JOB_HOLES]
    rows = query(
        "SELECT hole_id, nvcl_available, raw_available, coalesce(raw_size_bytes,0) AS size, "
        "processed_status, raw_status FROM explorer_hole WHERE hole_id = ANY(%s)",
        (holes,),
    )
    nvcl = [r for r in rows if r["nvcl_available"]]
    raw_rows = [r for r in rows if r["raw_available"]]
    zip_bytes = sum(r["size"] for r in raw_rows)
    free = shutil.disk_usage(DATA_DIR).free if DATA_DIR.exists() else None
    return {
        "holes": len(rows),
        "processed": {
            "holes": len(nvcl),
            "already_prepared": sum(r["processed_status"] == "prepared" for r in nvcl),
            "estimated_minutes": round(len(nvcl) * 17 / 4 / 60, 1),
            "estimated_bytes": len(nvcl) * 30_000_000,  # CSV + Parquet + source snapshot
        },
        "raw": {
            "holes": len(raw_rows),
            "already_summarised": sum(
                str(r["raw_status"]).startswith("summarised") for r in raw_rows
            ),
            "zip_bytes": zip_bytes,
            "spectral_only_transfer_bytes": int(zip_bytes * 0.14),
            "spectral_only_disk_bytes": int(zip_bytes * 0.30),
            "summary_bytes": len(raw_rows) * 3_000_000,
            "estimated_minutes_spectral_only": round(zip_bytes * 0.14 / 2e6 / 60 / 2, 1),
        },
        "free_bytes": free,
    }


def _job_row(row: dict) -> dict:
    row["job_id"] = str(row["job_id"])
    if row.get("parent_id"):
        row["parent_id"] = str(row["parent_id"])
    return row


@app.post("/api/v2/jobs")
def create_job(payload: Annotated[dict, Body()]):
    kind = payload.get("kind")
    params = payload.get("params") or {}
    if kind not in LANES:
        raise HTTPException(422, f"Unknown job kind {kind!r}")
    holes = params.get("holes")
    if holes is not None:
        if not isinstance(holes, list) or len(holes) > MAX_JOB_HOLES:
            raise HTTPException(422, f"holes must be a list of at most {MAX_JOB_HOLES} IDs")
        known = {
            r["hole_id"]
            for r in query("SELECT hole_id FROM explorer_hole WHERE hole_id=ANY(%s)", (holes,))
        }
        unknown = [h for h in holes if h not in known]
        if unknown:
            raise HTTPException(422, f"Unknown holes: {', '.join(unknown[:10])}")
    if kind == "raw_fetch" and params.get("mode", "spectral_only") not in (
        "spectral_only",
        "full_zip",
    ):
        raise HTTPException(422, "mode must be spectral_only or full_zip")
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO job(kind,lane,params,priority) VALUES (%s,%s,%s,%s) RETURNING *",
            (kind, LANES[kind], Jsonb(params), int(payload.get("priority", 100))),
        ).fetchone()
    return clean(_job_row(row))


@app.get("/api/v2/jobs")
def list_jobs(limit: int = Query(50, le=200)):
    rows = query(
        "SELECT job_id, kind, lane, status, params, progress, error, created_at, started_at, "
        "finished_at, parent_id FROM job ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    for row in rows:
        holes = row["params"].get("holes") if row["params"] else None
        if holes and len(holes) > 12:
            row["params"] = {**row["params"], "holes": holes[:12], "holes_total": len(holes)}
    return clean([_job_row(r) for r in rows])


@app.get("/api/v2/jobs/{job_id}")
def job_detail(job_id: str):
    row = one("SELECT * FROM job WHERE job_id::text=%s", (job_id,))
    if row is None:
        raise HTTPException(404, "unknown job")
    logs = query(
        "SELECT ts, level, message FROM job_log WHERE job_id::text=%s ORDER BY id DESC LIMIT 100",
        (job_id,),
    )
    return clean({"job": _job_row(row), "logs": logs[::-1]})


@app.post("/api/v2/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    row = one(
        """UPDATE job SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END,
                  finished_at=CASE WHEN status='queued' THEN now() ELSE finished_at END
           WHERE job_id::text=%s AND status IN ('queued','running') RETURNING status""",
        (job_id,),
    )
    return {"status": row["status"] if row else "not_cancellable"}


_storage_cache: dict = {"value": None, "running": False}
STORAGE_FOLDERS = ("downloads/processed", "downloads/raw", "snapshots", "products", "reference")


def _measure_storage() -> None:
    """Folder sizes walk many files through the bind mount, so run off the request path."""
    import threading

    def work() -> None:
        try:
            folders = {}
            for name in STORAGE_FOLDERS:
                total = 0
                for base, _, files in os.walk(DATA_DIR / name):
                    for file in files:
                        try:
                            total += os.stat(os.path.join(base, file)).st_size
                        except OSError:
                            pass
                folders[name] = total
            _storage_cache.update(value=folders, at=time.time())
        finally:
            _storage_cache["running"] = False

    if not _storage_cache["running"]:
        _storage_cache["running"] = True
        threading.Thread(target=work, daemon=True).start()


@app.get("/api/v2/storage")
def storage():
    if _storage_cache.get("at", 0) < time.time() - 600:
        _measure_storage()
    usage = shutil.disk_usage(DATA_DIR) if DATA_DIR.exists() else None
    return {
        "data_dir": os.environ.get("HYLOGGER_DATA_HOST", str(DATA_DIR)),
        "total_bytes": usage.total if usage else None,
        "free_bytes": usage.free if usage else None,
        "folders": _storage_cache["value"] or {},
        "measuring": _storage_cache["running"],
    }


@app.get("/api/v2/method")
def method():
    runs = query(
        "SELECT run_id, kind, sensor, card, created_at FROM analysis_run WHERE status='active' "
        "ORDER BY kind, sensor"
    )
    for run in runs:
        card = run["card"]
        for key in (
            "features",
            "minerals",
            "mineral_groups",
            "class_profiles",
            "grid_nm",
            "cluster_names",
        ):
            card.pop(key, None)
    load = one(
        "SELECT loaded_at, sha256, build, row_counts FROM ref_load ORDER BY loaded_at DESC LIMIT 1"
    )
    return clean({"runs": runs, "reference": load, "oxides": list(OXIDES)})


@app.get("/api/v2/mineral-groups")
def mineral_groups():
    rows = query(
        "SELECT DISTINCT s.sensor, g.key AS mineral, g.value AS grp FROM feature_set s "
        "JOIN active_feature_set a USING (feature_set_id), jsonb_each_text(s.qc->'mineral_groups') g"
    )
    out: dict = {}
    for row in rows:
        out.setdefault(row["sensor"], {})[row["mineral"]] = row["grp"]
    return out


# --------------------------------------------------------------- frontend
from backend.legacy import app as legacy_app  # noqa: E402

app.mount("/legacy", legacy_app)
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "explorer.html")

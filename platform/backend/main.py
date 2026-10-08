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
HOLE_VIEWS = ("combined", "swir", "tir", "raw_nir", "raw_tir")
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
def map_holes(view: str = Query("combined", pattern="^(combined|swir|tir|raw_nir|raw_tir)$")):
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
    record = one("SELECT wamex_collar_id FROM explorer_hole WHERE hole_id=%s", (hole_id,))
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
    return clean(
        {
            "hole_id": hole_id,
            "tracks": tracks,
            "assays": chemistry,
            "analytes": [a for a in ANALYTES if any(a in row for row in chemistry)],
            "lithology": lithology,
            "raw": _raw_bins(hole_id),
            "notes": {
                "abundance": "Mean normalised TSG component weight per diagnostic reading; not mass %",
                "assays": "GSWA interim package (unverified); negatives (below detection) shown as half "
                "the limit; oxides from element ppm where no oxide was reported",
            },
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


# ---------------------------------------------------------- similarity etc.
@app.get("/api/v2/similarity")
def similarity(
    holes: str, view: str = Query("combined", pattern="^(combined|swir|tir|raw_nir|raw_tir)$")
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
    view: str = Query("combined", pattern="^(combined|swir|tir|raw_nir|raw_tir)$"),
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
            mix.index = [str(c).removeprefix("mineral_").replace("class_", "class ") for c in mix.index]
            cluster_names[int(cluster)] = name_profile(mix, dominant_share=0.5, parts=3)
    return clean({
        "view": view,
        "run_id": run["run_id"],
        "k": int(len(np.unique(labels))),
        "assignments": dict(zip(names, labels.tolist(), strict=True)),
        "names": cluster_names,
        "sizes": sizes,
    })


@app.get("/api/v2/holes/{hole_id}/similar")
def similar_holes(
    hole_id: str,
    view: str = Query("combined", pattern="^(combined|swir|tir|raw_nir|raw_tir)$"),
    limit: int = Query(15, le=50),
):
    run = active_run("hole", view)
    if run is None:
        return {"view": view, "items": []}
    rows = query(
        """SELECT n.neighbor_id AS hole_id, n.distance, n.rank, h.commodity, h.tectonic_region,
                  h.lat, h.lon, p.cluster_id, p.summary,
                  ST_Distance(h.geom, s.geom) / 1000 AS km
           FROM hole_neighbor n JOIN explorer_hole h ON h.hole_id=n.neighbor_id
           JOIN explorer_hole s ON s.hole_id=n.hole_id
           LEFT JOIN hole_profile p ON p.run_id=n.run_id AND p.hole_id=n.neighbor_id
           WHERE n.run_id=%s AND n.hole_id=%s ORDER BY n.rank LIMIT %s""",
        (run["run_id"], hole_id, limit),
    )
    me = one(
        "SELECT summary FROM hole_profile WHERE run_id=%s AND hole_id=%s", (run["run_id"], hole_id)
    )
    mine = (me or {}).get("summary", {})
    for row in rows:
        row["why"] = _why(mine, row.get("summary") or {}, view)
    return clean(
        {
            "view": view,
            "run_id": run["run_id"],
            "distance": run["card"].get("distance"),
            "items": rows,
        }
    )


def _why(a: dict, b: dict, view: str) -> list[str]:
    if view == "combined":
        a, b = a.get("swir") or a.get("tir") or {}, b.get("swir") or b.get("tir") or {}
    ma = dict(sorted(a.get("top_minerals", {}).items(), key=lambda kv: -kv[1]))
    mb = dict(sorted(b.get("top_minerals", {}).items(), key=lambda kv: -kv[1]))
    shared = [m for m in ma if m in mb][:3]
    diffs = sorted(set(ma) | set(mb), key=lambda m: -abs(ma.get(m, 0) - mb.get(m, 0)))[:2]
    out = [f"both rich in {', '.join(shared)}"] if shared else []
    out += [
        f"{m}: {ma.get(m, 0):.0%} vs {mb.get(m, 0):.0%}"
        for m in diffs
        if abs(ma.get(m, 0) - mb.get(m, 0)) > 0.05
    ]
    return out


@app.get("/api/v2/embedding")
def embedding(view: str = Query("combined", pattern="^(combined|swir|tir|raw_nir|raw_tir)$")):
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
    return clean(
        {"sensor": sensor, "run_id": run["run_id"], "card": card, "profiles": profiles, "raw": raw}
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
                    "lat": float(holes.lat.iloc[index[j]]),
                    "lon": float(holes.lon.iloc[index[j]]),
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

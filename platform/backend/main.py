"""HyLogger platform API.

    uvicorn backend.main:app --reload --port 8000

Endpoints:
    GET /api/health              is the DB up?
    GET /api/holes               holes as GeoJSON, with filters
    GET /api/holes/{hole_id}     one hole
    GET /api/filters             distinct values, to populate the UI dropdowns
    GET /api/stats               headline numbers
    GET /                        the map page

The rule this file exists to enforce: the API only READS precomputed tables.
No model runs here, no call to GSWA. That is what keeps responses in
milliseconds and demos safe when the venue wifi dies.
"""

import csv
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://hylogger:hylogger@localhost:5433/hylogger"
)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
SPECTRAL_PILOT_DIR = Path(os.environ.get("SPECTRAL_PILOT_DIR", "data/system/spectral"))
COMPARISON_DIR = Path(os.environ.get("COMPARISON_DIR", "data/system/comparisons"))

app = FastAPI(
    title="GSWA HyLogger Platform",
    description="Read-only API over precomputed HyLogger drill-hole data.",
    version="0.1.0",
)


@contextmanager
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


@app.get("/api/health")
def health():
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM hole")
            return {"status": "ok", "holes": cur.fetchone()["n"]}
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {exc}") from exc


@app.get("/api/stats")
def stats():
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)                                        AS holes,
                   count(*) FILTER (WHERE NOT length_is_valid)     AS holes_bad_length,
                   -- only sum lengths we believe, so one corrupt row cannot
                   -- move the headline number
                   coalesce(sum(length_m) FILTER (WHERE length_is_valid), 0) AS total_metres,
                   count(DISTINCT operator)                        AS operators,
                   count(DISTINCT project)                         AS projects
            FROM hole
            """
        )
        return cur.fetchone()


@app.get("/api/filters")
def filters():
    """Distinct values for the UI controls, so the front-end never hardcodes them.

    Only fields with more than one distinct value are worth offering as a filter.
    GSWA reports drilling_method = 'Diamond Core' for all 1,897 holes, so it is
    reported here as a constant rather than rendered as a dead dropdown.
    """
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT drilling_method AS value, count(*) AS n FROM hole GROUP BY 1 ORDER BY n DESC"
        )
        methods = cur.fetchall()

        cur.execute(
            "SELECT project AS value, count(*) AS n FROM hole WHERE project IS NOT NULL "
            "GROUP BY project ORDER BY n DESC, project"
        )
        projects = cur.fetchall()

        cur.execute(
            "SELECT operator AS value, count(*) AS n FROM hole WHERE operator IS NOT NULL "
            "GROUP BY operator ORDER BY n DESC LIMIT 50"
        )
        operators = cur.fetchall()

        cur.execute(
            "SELECT min(length_m) AS min, max(length_m) AS max FROM hole WHERE length_is_valid"
        )
        length = cur.fetchone()

    return {
        "drilling_methods": methods,
        "drilling_method_is_constant": len(methods) <= 1,
        "projects": projects,
        "top_operators": operators,
        "length_m": length,
    }


@app.get("/api/holes")
def holes(
    min_length: float | None = Query(None, description="metres"),
    max_length: float | None = Query(None, description="metres"),
    method: str | None = None,
    project: str | None = None,
    operator: str | None = Query(None, description="case-insensitive substring"),
    include_invalid_length: bool = Query(
        True, description="keep holes whose recorded length is missing or <= 0"
    ),
    near_lat: float | None = Query(None, description="with near_lon + radius_km"),
    near_lon: float | None = None,
    radius_km: float | None = None,
    limit: int = Query(5000, le=20000),
):
    """Holes as a GeoJSON FeatureCollection — the format Leaflet consumes directly."""
    where, params = ["TRUE"], []

    # A hole with an unusable length must not vanish just because a numeric
    # filter is active. The caller opts in or out explicitly.
    if min_length is not None or max_length is not None:
        clause = "(length_is_valid"
        if min_length is not None:
            clause += " AND length_m >= %s"
            params.append(min_length)
        if max_length is not None:
            clause += " AND length_m <= %s"
            params.append(max_length)
        clause += ")"
        if include_invalid_length:
            clause = f"({clause} OR NOT length_is_valid)"
        where.append(clause)
    elif not include_invalid_length:
        where.append("length_is_valid")

    if method:
        where.append("drilling_method = %s")
        params.append(method)

    if project:
        where.append("project = %s")
        params.append(project)

    if operator:
        where.append("operator ILIKE %s")
        params.append(f"%{operator}%")

    if near_lat is not None and near_lon is not None and radius_km:
        # Indexed spatial query — this is what PostGIS is here for.
        where.append("ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)")
        params.extend([near_lon, near_lat, radius_km * 1000])

    params.append(limit)

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT h.hole_id, h.lat, h.lon, h.length_m, h.length_is_valid,
                   h.drilling_method, h.operator, h.project,
                   coalesce(c.regions, ARRAY[]::text[]) AS processed_regions
            FROM hole h
            LEFT JOIN (
                SELECT hole_id, array_agg(DISTINCT region) AS regions
                FROM interval_product GROUP BY hole_id
            ) c ON c.hole_id=h.hole_id
            WHERE {" AND ".join(where)}
            ORDER BY hole_id
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()

    return {
        "type": "FeatureCollection",
        "count": len(rows),
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {k: v for k, v in r.items() if k not in ("lat", "lon")},
            }
            for r in rows
        ],
    }


@app.get("/api/holes/{hole_id}")
def hole_detail(hole_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM hole WHERE hole_id = %s", (hole_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, f"no hole called {hole_id!r}")
        row.pop("geom", None)  # binary, not useful over JSON

        cur.execute(
            """
            SELECT depth_from_m, depth_to_m, mineral_group, weight, n_readings
            FROM hole_interval WHERE hole_id = %s
            ORDER BY depth_from_m, mineral_group
            """,
            (hole_id,),
        )
        row["intervals"] = cur.fetchall()

    return row


def _current_model(cur, region: str, requested: str | None) -> str | None:
    if requested:
        cur.execute(
            "SELECT model_id FROM model_run WHERE model_id=%s AND region=%s", (requested, region)
        )
    else:
        cur.execute("SELECT model_id FROM active_model WHERE region=%s", (region,))
    row = cur.fetchone()
    if requested and row is None:
        raise HTTPException(404, "model version unavailable for region")
    return row["model_id"] if row else None


@app.get("/api/system/coverage")
def system_coverage():
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT hole_id, region, count(*) AS intervals,
                   sum(n_total) AS readings, sum(n_admitted) AS admitted
                   FROM interval_product GROUP BY hole_id,region ORDER BY hole_id,region""")
        return cur.fetchall()


@app.get("/api/system/models")
def system_models():
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT m.model_id,m.region,m.card,a.model_id IS NOT NULL AS active
                   FROM model_run m LEFT JOIN active_model a ON m.model_id=a.model_id
                   ORDER BY m.loaded_at DESC""")
        return cur.fetchall()


@app.get("/api/system/comparisons/team-20/{region}")
def team_20_comparison(region: str):
    """Precomputed hole-level comparison, separate from interval model labels."""
    if region not in {"swir", "tir"}:
        raise HTTPException(404, "sensor region unavailable")
    path = COMPARISON_DIR / f"team_20_{region}.json"
    if not path.is_file():
        raise HTTPException(404, "20-hole comparison has not been generated")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/system/runs")
def system_runs(limit: int = Query(10, ge=1, le=100)):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT batch_id,summary,recorded_at FROM batch_run ORDER BY recorded_at DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()


@app.get("/api/system/spectral/{region}/{hole_id}/{depth_from_m}")
def spectral_pilot(region: str, hole_id: str, depth_from_m: float):
    """One precomputed adjusted-TSG spectrum and its pilot representative."""
    if region not in {"swir", "tir"}:
        raise HTTPException(404, "sensor region unavailable")
    root = SPECTRAL_PILOT_DIR / region
    view_path = root / "spectral_view.json"
    card_path = root / "model_card.json"
    if not view_path.is_file() or not card_path.is_file():
        raise HTTPException(404, "no spectral pilot artifact for this region")
    card = json.loads(card_path.read_text(encoding="utf-8"))
    if hole_id != card["inference_hole"]:
        raise HTTPException(404, "hole is outside this spectral inference pilot")
    view = json.loads(view_path.read_text(encoding="utf-8"))
    observation = next(
        (item for item in view["observations"] if item["depth_from_m"] == depth_from_m), None
    )
    if observation is None:
        raise HTTPException(404, "no spectral interval at this depth")
    return {
        "region": region,
        "hole_id": hole_id,
        "wavelengths": view["wavelengths"],
        "observation": observation,
        "representative_mean": view["cluster_mean"][str(observation["cluster_id"])],
        "model_card": {
            k: card[k]
            for k in (
                "development_hole",
                "inference_hole",
                "selected_k",
                "transforms",
                "training_distance_99pct",
                "status",
                "source_files",
            )
        },
    }


@app.get("/api/system/holes/{hole_id}/intervals")
def system_intervals(
    hole_id: str,
    region: str = Query("swir", pattern="^(swir|tir)$"),
    model_id: str | None = None,
    from_m: float | None = None,
    to_m: float | None = None,
    limit: int = Query(2000, ge=1, le=10000),
):
    with db() as conn, conn.cursor() as cur:
        selected = _current_model(cur, region, model_id)
        cur.execute(
            """SELECT i.*,a.cluster_id,a.distance,a.status AS assignment_status,
                   e.qc->>'mask_status' AS mask_status
                   FROM interval_product i JOIN etl_run e USING(interval_set_id)
                   LEFT JOIN interval_assignment a ON a.interval_id=i.interval_id AND a.model_id=%s
                   WHERE i.hole_id=%s AND i.region=%s
                     AND (%s::float8 IS NULL OR i.depth_to_m>%s)
                     AND (%s::float8 IS NULL OR i.depth_from_m<%s)
                   ORDER BY i.depth_from_m LIMIT %s""",
            (selected, hole_id, region, from_m, from_m, to_m, to_m, limit),
        )
        rows = cur.fetchall()
        return {"hole_id": hole_id, "region": region, "model_id": selected, "intervals": rows}


@app.get("/api/system/intervals/{interval_id}")
def system_interval(interval_id: str, model_id: str | None = None):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT i.*,e.qc FROM interval_product i "
            "JOIN etl_run e USING(interval_set_id) WHERE interval_id=%s",
            (interval_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "interval unavailable")
        selected = _current_model(cur, row["region"], model_id)
        cur.execute(
            "SELECT cluster_id,distance,status FROM interval_assignment "
            "WHERE interval_id=%s AND model_id=%s",
            (interval_id, selected),
        )
        row["assignment"] = cur.fetchone()
        row["model_id"] = selected
        return row


@app.get("/api/system/intervals/{interval_id}/similar")
def system_similar(
    interval_id: str, model_id: str | None = None, limit: int = Query(10, ge=1, le=100)
):
    # Bounded same-cluster distance ranking, labelled as a ranking rather than probability.
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT region FROM interval_product WHERE interval_id=%s", (interval_id,))
        source = cur.fetchone()
        if source is None:
            raise HTTPException(404, "interval unavailable")
        selected = _current_model(cur, source["region"], model_id)
        cur.execute(
            """SELECT i.interval_id,i.hole_id,i.region,i.depth_from_m,i.depth_to_m,
                   a.model_id,a.cluster_id,a.distance
                   FROM interval_assignment a JOIN interval_product i USING(interval_id)
                   WHERE a.model_id=%s
                      AND a.cluster_id=(SELECT cluster_id FROM interval_assignment
                                        WHERE interval_id=%s AND model_id=%s)
                     AND i.interval_id<>%s ORDER BY a.distance LIMIT %s""",
            (selected, interval_id, selected, interval_id, limit),
        )
        return {
            "model_id": selected,
            "method": "same cluster, ranked by distance to centroid",
            "items": cur.fetchall(),
        }


@app.get("/api/system/holes/{hole_id}/export.csv")
def system_export(
    hole_id: str,
    region: str = "swir",
    model_id: str | None = None,
    from_m: float | None = None,
    to_m: float | None = None,
):
    result = system_intervals(hole_id, region, model_id, from_m, to_m, 10000)
    buffer = io.StringIO()
    columns = [
        "interval_id",
        "interval_set_id",
        "hole_id",
        "dataset_id",
        "region",
        "depth_from_m",
        "depth_to_m",
        "n_total",
        "n_admitted",
        "dominant_group",
        "cluster_id",
        "distance",
        "assignment_status",
        "model_id",
        "features",
        "source_rows",
        "reason_counts",
    ]
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in result["intervals"]:
        writer.writerow(
            {
                name: (
                    json.dumps(row[name])
                    if name in ("features", "source_rows", "reason_counts")
                    else result["model_id"]
                    if name == "model_id"
                    else row.get(name)
                )
                for name in columns
            }
        )
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{hole_id}_{region}.csv"'},
    )


# The map page. Mounted last so it cannot shadow the /api routes above.
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html")

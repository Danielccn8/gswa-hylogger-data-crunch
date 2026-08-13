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
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://hylogger:hylogger@localhost:5433/hylogger"
)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

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
        raise HTTPException(503, f"database unavailable: {exc}")


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
            "SELECT drilling_method AS value, count(*) AS n FROM hole "
            "GROUP BY 1 ORDER BY n DESC"
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
            "SELECT min(length_m) AS min, max(length_m) AS max FROM hole "
            "WHERE length_is_valid"
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
        where.append(
            "ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)"
        )
        params.extend([near_lon, near_lat, radius_km * 1000])

    params.append(limit)

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT hole_id, lat, lon, length_m, length_is_valid,
                   drilling_method, operator, project
            FROM hole
            WHERE {' AND '.join(where)}
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


# The map page. Mounted last so it cannot shadow the /api routes above.
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html")

# HyLogger Platform

Interactive map of every NVCL HyLogger-scanned drill hole in Western Australia.
PostGIS + FastAPI + Leaflet.

![stack](https://img.shields.io/badge/stack-PostGIS%20%7C%20FastAPI%20%7C%20Leaflet-blue)

---

## Quick start

You need **Docker Desktop** and **Python 3.11+**.

> **Every command below is run from the `platform/` directory, not the repository
> root.** `docker-compose.yml`, `etl/`, and `backend/` all live here, so running
> these from the root fails with `no configuration file provided: not found`.

### First time only

Install the platform dependencies (this one runs from the **repository root**,
because that is where `pyproject.toml` is):

```bash
python -m pip install -e ".[platform]"
```

Then fetch the hole catalog. `hylogger` is an installed command, so it works
from anywhere:

```bash
hylogger catalog refresh
```

### Starting the map

**1. Start Docker Desktop** and wait for the whale icon to stop animating. If it
is not running, the next command fails with `failed to connect to the docker API`.

Activate the project's virtual environment in the same terminal — `uvicorn` and
`hylogger` are installed inside it, so without this you get `command not found`:

```bash
.venv\Scripts\activate
```

(macOS or Linux: `source .venv/bin/activate`. Your prompt should now start with
`(.venv)`.)

**2. Start the database:**

```bash
cd platform && docker compose up -d
```

**3. Load the holes into it** (safe to re-run; takes a few seconds):

```bash
python etl/load_holes.py
```

**4. Start the API and map:**

```bash
uvicorn backend.main:app --reload --port 8000
```

**5. Open <http://localhost:8000>.** API docs are at <http://localhost:8000/docs>.

Leave the `uvicorn` window open — it is the server. `Ctrl+C` in that window
stops the map.

### Stopping

`Ctrl+C` the `uvicorn` window, then:

```bash
cd platform && docker compose down
```

Your loaded data survives `docker compose down`, so next time you only need
steps 1, 2 and 4. Add `-v` to delete the database volume as well — after that
you must run `python etl/load_holes.py` again.

### Is it working?

```bash
curl http://localhost:8000/api/health
```

Expect `{"status":"ok","holes":1897}`. If `holes` is 0, run step 3.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no configuration file provided: not found` | You are in the repository root | `cd platform` first |
| `failed to connect to the docker API` | Docker Desktop is not running | Start it, wait for it to settle |
| `catalog.csv not found` | Catalog never fetched | `hylogger catalog refresh` |
| `database unavailable` from `/api/health` | Container not up | `docker compose up -d` |
| Page loads but no pins, no basemap | Leaflet and the tiles come from a CDN | Needs internet; the API itself works offline |
| Port 8000 already in use | Another server is running | `--port 8001` |

---

## How it fits together

```
NVCL API + THREDDS  ──►  hylogger catalog refresh  ──►  data/catalog/catalog.csv
                                                               │
                                                               ▼
                                                        etl/load_holes.py
                                                   │
                                                   ▼
                            PostgreSQL + PostGIS  (hole, hole_interval, hole_feature)
                                                   │
                                                   ▼
                                     backend/main.py  (FastAPI, read-only)
                                                   │
                                                   ▼
                                    frontend/index.html  (Leaflet)
```

**The rule this design exists to enforce:** the API only reads precomputed tables. It never runs
a model and never calls GSWA. Every request is an indexed query — single-digit milliseconds — and
a demo cannot fail because the venue wifi died.

---

## Layout

| Path | What |
|---|---|
| `docker-compose.yml` | PostGIS 16-3.4 on host port **5433** |
| `db/schema.sql` | Tables, indexes, comments. Applied automatically on first start. |
| `etl/load_holes.py` | Combined local catalog → `hole` table. Idempotent (upsert). Preserves holes absent from an incomplete snapshot; explicit `--prune` removes them after review. |
| `backend/main.py` | FastAPI. GeoJSON endpoints + the map page. |
| `frontend/index.html` | Leaflet map, marker clustering, filters. No build step. |

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Is the DB reachable, and how many holes are loaded |
| `GET /api/stats` | Headline numbers for the UI |
| `GET /api/filters` | Distinct values so the front-end hardcodes nothing |
| `GET /api/holes` | GeoJSON FeatureCollection, filterable |
| `GET /api/holes/{id}` | One hole plus its depth intervals |

`/api/holes` accepts `min_length`, `max_length`, `project`, `operator`,
`include_invalid_length`, and `near_lat` + `near_lon` + `radius_km` for spatial search.

---

## Data notes you will trip over

These are measured facts about GSWA's data, not hypotheticals.

- **Hole `YGD4` reports a length of −115,224 m.** Corrupt at source. We load it, flag it with
  `length_is_valid = false`, and exclude it from sums — we do not silently drop or "fix" the
  client's data.
- **`drilling_method` is constant.** All 1,897 holes say `Diamond Core`, so it is useless as a
  filter. The UI reports this instead of offering a dropdown that always returns everything.
- **Not all holes are mineral exploration.** `Petrel 1-7` and `Tern 3-5` are offshore petroleum
  wells in the Timor Sea, ~1,300 km north of the nearest mineral hole. Decide explicitly whether
  they are in scope.
- **`ST_MakePoint` takes (lon, lat)**, in that order. Swap them and every hole lands off the coast
  of Somalia. `etl/load_holes.py` has a comment where it matters.
- **The catalog spreadsheet and the live API disagree** — 1,951 vs 1,897 holes, with a few hundred
  unique to each. Unresolved; a question for GSWA.

## What is not built yet

`hole_interval` and `hole_feature` exist in the schema but are empty. Filling them is Phase 1 and
Phase 2 — see `Architectural Proposal` in the team's notes.

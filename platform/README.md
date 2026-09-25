# HyLogger Platform

Interactive map of every NVCL HyLogger-scanned drill hole in Western Australia.
PostGIS + FastAPI + Leaflet.

![stack](https://img.shields.io/badge/stack-PostGIS%20%7C%20FastAPI%20%7C%20Leaflet-blue)

---

## Quick start

To view an already loaded local demo, you need **Docker Desktop**. Run the
commands below from the **repository root**. Python 3.11+ and the project venv
are needed when acquiring or reprocessing data, not just to view the map.

### Starting the map

Start Docker Desktop, then in PowerShell:

```powershell
Set-Location -LiteralPath 'C:\Users\DCCN9\.codex\worktrees\hylogger-data-crunch-system'
docker compose -p hylogger_system -f platform/docker-compose.yml up -d --build
docker compose -p hylogger_system -f platform/docker-compose.yml ps
```

With no service name after `up`, Compose starts **both** services in this file:
`db` (PostGIS on `127.0.0.1:5433`) and `api` (FastAPI plus the Leaflet page on
`127.0.0.1:8000`). `-d` keeps them running in the background. `--build` builds
the API image. The API waits until the database health check passes.

Open <http://localhost:8000>. API docs are at <http://localhost:8000/docs>.
The existing named database volume keeps previously loaded holes and intervals.
The Compose command alone does not download or cluster new source data; use
`docs/PIPELINE_GUIDE.md` for the separate ETL and model commands.

### Stopping

```powershell
docker compose -p hylogger_system -f platform/docker-compose.yml stop
```

This stops only the two owned containers and preserves the database volume.

### Is it working?

```powershell
Invoke-RestMethod 'http://localhost:8000/api/health'
```

Expect `{"status":"ok","holes":1897}` for the current local snapshot. If
`holes` is 0 on a new database, acquire a catalog and load it with
`platform/etl/load_holes.py` as documented in `docs/PIPELINE_GUIDE.md`.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no configuration file provided: not found` | Compose file was not specified | Run from the repository root with `-f platform/docker-compose.yml` |
| `failed to connect to the docker API` | Docker Desktop is not running | Start it, wait for it to settle |
| `catalog.csv not found` | Catalog never fetched | Follow the acquisition steps in the root README |
| `database unavailable` from `/api/health` | Database container is not healthy | Check `docker compose -p hylogger_system -f platform/docker-compose.yml ps` |
| Page loads but no pins, no basemap | Leaflet and the tiles come from a CDN | Needs internet; the API itself works offline |
| Port 8000 already in use | Another server is running | Set `$env:HYLOGGER_API_PORT='8001'` before `up`; open port 8001 |

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

# HyLogger Platform

Interactive map of every NVCL HyLogger-scanned drill hole in Western Australia.
PostGIS + FastAPI + Leaflet.

![stack](https://img.shields.io/badge/stack-PostGIS%20%7C%20FastAPI%20%7C%20Leaflet-blue)

---

## Quick start

To view an already loaded local demo, you need **Docker Desktop**. Run the
commands below from the **repository root**. Python 3.11+ and the project venv
are needed when acquiring or reprocessing data, not just to view the map.

### Starting the analytical dashboard

Start Docker Desktop, then in PowerShell:

```powershell
Set-Location -LiteralPath 'C:\Users\DCCN9\.codex\worktrees\hylogger-data-crunch-system'
docker compose -p hylogger_system -f platform/docker-compose.yml up -d --build
docker compose -p hylogger_system -f platform/docker-compose.yml ps
```

With no service name after `up`, Compose starts **both** services in this file:
`db` (PostGIS on `127.0.0.1:5433`) and `api` (FastAPI plus the analytical dashboard on
`127.0.0.1:8000`). `-d` keeps them running in the background. `--build` builds
the API image. The API waits until the database health check passes.

Open <http://127.0.0.1:8000>. API docs are at <http://127.0.0.1:8000/docs>.
The existing named database volume keeps previously loaded holes and intervals.
The Compose command alone does not download or cluster new source data; use
`docs/delivery/LOCAL_DELIVERY_RUNBOOK.md` for acquisition, migration, ETL and
verification commands. The selected20 dataset has both sensors; All prepared
holes exposes the separate 50-hole capacity checkpoint. See
`docs/SYSTEM_EXPLAINED.md` for the compact data/code/model explanation.

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
| Map tiles unavailable | Basemap needs internet | Leaflet is bundled; coordinates, tables, profiles and comparison remain available |
| Dashboard fails on an older database | Active-set migration/summaries missing | Apply migration 003 and run the delivery ETL; see the runbook |
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
                         frontend/dashboard.html + app.js + app.css
                            (Overview / Compare / Map / Method)
```

The API reads prepared products and summaries; it never trains a model or calls
GSWA. Similarity computes distances from stored features. The local database
must be running; loss of external map tiles does not prevent analytical use.

---

## Layout

| Path | What |
|---|---|
| `docker-compose.yml` | PostGIS 16-3.4 on host port **5433** |
| `db/schema.sql` | Tables, indexes, comments. Applied automatically on first start. |
| `etl/load_holes.py` | Combined local catalog → `hole` table. Idempotent (upsert). Preserves holes absent from an incomplete snapshot; explicit `--prune` removes them after review. |
| `backend/main.py` | FastAPI. GeoJSON endpoints + the map page. |
| `frontend/dashboard.html`, `app.js`, `app.css` | Four connected analytical views; no frontend build step. |
| `frontend/index.html` | Preserved historical map-first viewer. |
| `db/migrations/003_active_interval_sets.sql` | Active source revisions and stored dashboard summaries. |

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/system/dashboard` | Prepared hole summaries, source coverage and active model card |
| `GET /api/system/holes/{id}/intervals` | Paginated, version-specific depth profile |
| `GET /api/system/holes/{id}/export.csv` | Same source/model and applied depth range |
| `GET /api/system/intervals/{id}/similar` | Actual distance to the selected interval |
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

## Remaining acceptance

Current interval products use `interval_product`, not the older `hole_interval`
and `hole_feature` placeholders. Wider raw acquisition, the 200-hole checkpoint,
independent geological review and second-machine/client acceptance remain open;
see `docs/delivery/EVIDENCE_2026-10-03.md`.

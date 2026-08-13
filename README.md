# GSWA HyLogger — Data Crunch

UWA CITS5553 Data Science Capstone · Group 7 · Client: **Geological Survey of Western Australia**

Finding mineral patterns across ~1,900 hyperspectral drill-core scans spanning Western Australia,
and making them explorable by a geoscientist in minutes rather than months.

---

## Repository map

| Folder | What it is |
|---|---|
| `platform/` | **The product.** PostGIS + FastAPI + Leaflet. See [platform/README.md](platform/README.md). |
| `data_view/` | Exploration notebooks — how we worked out what the data actually is. |
| `data/` | Downloaded drill-core packages. **Not in git** (~940 MB, and it is the client's data). |

## Get running in five commands

Requires **Docker Desktop** and **Python 3.11+**.

```bash
pip install -r platform/requirements.txt
```

```bash
docker compose -f platform/docker-compose.yml up -d
```

```bash
python data_view/build_hole_index.py
```

```bash
python platform/etl/load_holes.py
```

```bash
cd platform && uvicorn backend.main:app --reload --port 8000
```

Open <http://localhost:8000> — a map of every HyLogger-scanned hole in WA.
API docs at <http://localhost:8000/docs>.

## Understand the data first

Start with the notebooks, in this order:

1. **`data_view/00_look_at_one_hole.ipynb`** — point it at a downloaded `.zip` and it shows you
   the tables and every column. Run this before anything else.
2. **`data_view/01_explore_the_data.ipynb`** — the full reference: what each dataset is, the
   traps, and the open questions for the client.

You will need a hole to look at. Grab any `.zip` from the
[NVCL THREDDS catalog](https://thredds.nci.org.au/thredds/catalog/rs07/WA/catalog.html) and drop
it in `data/`.

## Traps that will cost you a day if you do not know them

- **`-3.402823e+38` means "missing"** in TSG files, not `NaN`. Unhandled, every average you compute
  is wrong and looks plausible.
- **Only 63% of readings have a mineral.** 28.6% are masked because the scanner was looking at the
  tray or a wooden depth block — not rock. Another 8% is real rock the software could not identify.
- **`INVALID` and `Aspectral` are not minerals.** They mean "don't know", and they rank among the
  most common results.
- **Depth is not sorted.** Core is scanned in parallel rows per tray, so depth steps backwards
  ~1,500 times per hole. Sort by depth before any window or join.
- **NIR and TIR disagree 94% of the time — correctly.** They see different mineral families. SWIR
  is blind to quartz; TIR reports it as the most abundant mineral in the hole.
- **Hole `YGD4` reports a length of −115,224 m.** Corrupt at source. Flagged, not dropped.

## Key libraries

Both are GSWA's own, recommended by the client:

- [`pytsg`](https://github.com/Geological-Survey-of-Western-Australia/pytsg) — reads `.tsg`/`.bip`
  spectral files
- [`nvcl-kit`](https://pypi.org/project/nvcl-kit/) — queries the live NVCL services (coordinates for
  all 1,897 holes in about a second, no download)

## Architecture

The design rule everything follows: **the API only reads precomputed tables.** No model runs at
request time, no call to GSWA at request time. Batch jobs do the expensive work and write results
to Postgres; every request is then an indexed query.

Full reasoning, the phased plan and the six workstreams live in the team's `Architectural Proposal`
note.

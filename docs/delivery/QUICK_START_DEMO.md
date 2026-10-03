# HyLogger: start and demonstrate the system

A short walkthrough for the existing installation on Daniel's machine. The prepared data and models are already loaded. Starting the app does not download data or retrain models.

## 1. Open the right folder and start

In VS Code, use **File > Open Folder**:

```text
C:\Users\DCCN9\.codex\worktrees\hylogger-data-crunch-system
```

The branch is **codex/hylogger-system**. This folder already has it checked out; open this folder rather than switching the original `Data Crunch` checkout from `main`.

Open **Docker Desktop** and wait until its engine is running. In VS Code's PowerShell terminal:

```powershell
cd "C:\Users\DCCN9\.codex\worktrees\hylogger-data-crunch-system"
docker compose -p hylogger_system -f platform/docker-compose.yml up -d --build
```

This starts the database and web app together. Open **http://127.0.0.1:8000**. Wait for **Local database connected**. No Python environment or notebook is needed for this demo.

## 2. Overview: explore the available data

Choose **Dataset: Selected 20 · three regions**, **Sensor: SWIR**, then **Reset filters**.

- Expect **20 visible drill holes** and **0 unavailable holes**.
- Explain the common TSG groups and reading support. Percentages describe interpretation frequencies, not mineral mass or prediction accuracy.
- Search **WKDD15001**: the table and statistics narrow to that hole.
- Press **Reset filters** before comparing or showing the full map.

## 3. Compare: inspect whole-hole profiles

With the search empty, tick these four rows in Overview:

**WKDD15001 · LD82625W1 · KEDD002 · PND003**

Open **Compare**. Each profile uses the same 0–100% scale. In SWIR, LD82625W1 is led by white mica; the other three are led by chlorite.

Change **Sensor** to **TIR**, return to Overview and tick the four holes again. TIR shows a different interpretation: WKDD15001 is led by amphibole; the other three by silica.

Changing sensor clears comparison selections and open depth detail. Searching also removes selected holes that are no longer visible. Select comparison rows with the search empty.

## 4. Depth detail: inspect and export intervals

In Overview, click **WKDD15001**. In its detail panel:

1. Enter **From depth: 100** and **To depth: 110**.
2. Click **Apply depth range**. Expect ten one-metre intervals.
3. Click **100.0–101.0 m**. Show its TSG frequencies, interval cluster, reading counts and **Similar intervals**.
4. Expand **How this interval was calculated** to show source rows and model identity.
5. Click **Export filtered intervals**. The CSV contains the ten selected intervals.

## 5. Raw, processed TSG, SWIR and TIR

**Processed TSG** supplies the interpretation labels used by Overview, Compare and interval clustering. **Raw-package spectra** provide the separate spectral pilot. **SWIR and TIR** are sensor ranges; choose them using the Sensor dropdown.

Within WKDD15001's **100–101 m** detail, scroll to **Adjusted spectrum · separate pilot**:

- Solid line: the interval's median spectrum. Dashed line: its pilot cluster's mean spectrum.
- Repeat step 4 after changing sensor to see the other spectral range.
- TIR at this depth reports **Assigned**. SWIR reports **Outside training distance**: a pilot coverage warning, not an application failure or geological discovery.

There is no global Raw/TSG switch. The displayed raw pilot was trained on LD82625W1 and applied to WKDD15001. Its SWIR view uses the NIR package, including VNIR + SWIR. Sharing a depth bin does not establish exact raw/API reading alignment.

## 6. Map: add geographic context

Click **Close detail**, **Reset filters**, then **Map**. Expect 20 mapped holes. Zoom, click a marker and select **Open depth profile**. **Study region** filters the map and statistics together. Clear that filter afterward. Internet is needed for the basemap tiles.

## 7. Data & method: explain the evidence

Open **Data & method**. Show coverage, excluded readings, the interval model and held-out holes. Expand **Model card and validation limits** for details. **Latest processing batch** covers all 50 requested holes, independently of the selected20 display. Clusters describe patterns; geological validation remains pending.

## 8. Stop after the demo

```powershell
docker compose -p hylogger_system -f platform/docker-compose.yml stop
```

The database and models remain available for the next start. Avoid deleting volumes.

If the page does not load, check Docker Desktop, then run:

```powershell
docker compose -p hylogger_system -f platform/docker-compose.yml ps
```

Both services should be running, with `db` healthy. If they are, refresh the page after a few seconds. Technical setup and reproduction commands are in [Local delivery runbook](LOCAL_DELIVERY_RUNBOOK.md).

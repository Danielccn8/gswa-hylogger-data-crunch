# Explorer v2 — standalone Windows delivery

## Recipient quick start

Download `HyLoggerExplorer.exe` from the repository's
[GitHub Releases](https://github.com/Danielccn8/gswa-hylogger-data-crunch/releases).
The repository and release are public.
Release assets include checksums and the corresponding source snapshot.

A new installation does not include the populated research database or the
1,000+ hole analysis shown in the presentation. Public acquisition and analysis
must complete before those views contain results. Coverage depends on the data
available when collection runs; exact research-snapshot counts are not guaranteed.

1. Copy `HyLoggerExplorer.exe` to the recipient's Windows 64-bit computer and open it.
2. Explorer creates its own database and opens the Data page in the default browser.
3. Click **Start / resume collection** once. It discovers the public Western Australia
   NVCL and NCI THREDDS catalogues, downloads missing processed logs and spectral
   members, prepares them, and periodically refreshes analysis.
4. **Pause collection** stops automatic acquisition. **Retry failed / refresh catalogue**
   starts another attempt for failed items and refreshes the catalogue.
5. Close using **Close Explorer and save progress** in the launcher window. Reopen
   the same EXE to continue. Closing only the browser leaves collection running.

No Python, Docker, PostgreSQL service installation or administrator access is required.
Collection starts disabled in a new installation. Opening Explorer does not start
downloading holes; the recipient enables collection using the button above.
Internet is needed for acquisition and online map tiles. Downloaded products remain
local and can be inspected offline. The application does not run while the computer
is off or the launcher is closed.

Default persistent folder: `%LOCALAPPDATA%\HyLoggerExplorer`. This holds the private
database, included database runtime, catalogue, products, progress and logs. Updating
the EXE preserves this folder. Do not delete it to update the application.

For a different drive, create a shortcut whose target is:

```text
HyLoggerExplorer.exe --data-dir "D:\HyLoggerData"
```

The default browser address is `http://127.0.0.1:8130`; a free port is selected if it
is occupied. The included database listens on loopback only with a generated local
password. A lock prevents two launchers using the same data folder. The existing
Docker Explorer at port 8100 is a separate installation and is not copied or changed.

## What is collected and retained

- Scope: public **WA** catalogues, not every Australian state. Totals are discovered,
  not hard-coded. Some catalogue records do not publish usable logs or both sensors.
- Processed: available NVCL scalar logs, joined only after depth alignment checks,
  and prepared mineral interval products.
- Raw: spectral members fetched with HTTP byte ranges; imagery is not requested.
  The automatic mode retains QC-admitted **1 m median spectra**, resampled wavelength
  grids, depth bins, quality reports, TSA version summaries and source checksums.
  These are reduced products, not lossless copies of every original reading.
- Every present sensor and persisted spectrum product must validate before managed
  raw sources are removed. An incomplete sensor keeps the source for investigation.
- Successful hole/source pairs are checkpointed. Interrupted jobs return to the queue;
  verified products can recover even if closure occurred after cleanup but before
  database registration. Individual failures retry at most three times, with backoff.
- Analysis runs after batches of 20 successful acquisitions or when the queue drains.
  Small initial collections may lack enough intervals/holes for a model; they remain
  viewable and the job log explains this. The last active analysis remains visible.

The restricted GSWA interim reference package and the developer's database, data,
credentials and configuration are **not distributed**. Assays, logged geology and
survey-dependent validation require a separately authorised reference import. Public
spectral exploration works without that package. Existing reference installations
retain their reference notices.

## Rebuild from code

On Windows x64, create the usual project venv, install the exact versions from
`platform/requirements.lock`, install the project, then:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller==6.22.3
.\.venv\Scripts\python.exe scripts/build_desktop.py
```

Output: `dist/HyLoggerExplorer.exe`. The build downloads official EDB PostgreSQL
16.15 and OSGeo PostGIS 3.6.2 binaries, assembles an embedded runtime ZIP, and bundles
Python, the worker, API and frontend. Sources and SHA-256 hashes are recorded in
`build/desktop-resources/vendor/runtime.json`. No geological data is needed to build.
Build/download caches and acceptance data are ignored by Git.

The executable is currently unsigned. Validation on the development Windows machine
includes a system-only PATH, startup, persistence and restart; a separate clean
Windows machine has not yet been tested.

PostgreSQL/PostGIS vendor licence files are retained in the embedded runtime.
Upstream source: https://www.postgresql.org/ftp/source/v16.15/ and
https://download.osgeo.org/postgis/source/postgis-3.6.2.tar.gz .
Application Python dependency versions are in `platform/requirements.lock`.

## Developer verification

Use a dedicated empty data directory, never a populated research database:

```powershell
dist\HyLoggerExplorer.exe --headless --no-browser --data-dir "C:\HyLoggerTest" --shutdown-after 120
```

The launcher writes `desktop-running.json` with the local URL, and detailed logs under
`logs/`. Startup errors go to `startup-error.log`. `desktop.json` contains a local
database credential; do not publish the data directory or include it in a release.

Automated coverage includes public-only bootstrap, preserving existing products on
catalogue refresh, duplicate prevention, pause/resume, bounded retries, unavailable
logs, analysis coalescing, failed-sensor retention, product read-back validation and
recovery after source cleanup. Database tests use disposable isolated schemas via
`HYLOGGER_TEST_DATABASE_URL`. Unit download tests use local fixtures, not providers.

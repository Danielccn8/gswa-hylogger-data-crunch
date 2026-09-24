# Local handoff / resumption

Worktree: `C:\Users\DCCN9\.codex\worktrees\hylogger-data-crunch-system`
Branch: `codex/hylogger-system`, based on `origin/main` `1cd4ffc`.
Original checkout: `C:\Users\DCCN9\OneDrive - UWA\Data Science\4-Semester July-November 2026\Capstone\Data Crunch`, left clean on `main` `3ba3337`.

Owned local Compose project: `hylogger_system` (API localhost:8000, PostGIS
localhost:5433). Generated artifacts and QC: ignored `data/system`. Backup:
`data/system/hylogger_26.dump`; isolated restore DB `hylogger_restore_26` inside
the owned PostGIS container. Source processed and raw data remain only in the
original checkout's `data` directory. Do not use `docker compose down -v` or
the catalogue loader's `--prune` against a partial snapshot.

```powershell
cd C:\Users\DCCN9\.codex\worktrees\hylogger-data-crunch-system
docker compose -p hylogger_system -f platform/docker-compose.yml up -d --build
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/compare_hdbscan.py
.\.venv\Scripts\python.exe scripts/run_spectral_pilot.py
```

To stop only this owned stack, use
`docker compose -p hylogger_system -f platform/docker-compose.yml stop`.
For a reviewed new cohort, estimate source bytes first, then run
`platform/etl/run_system.py --mode infer` with explicit frozen model IDs
`6f1aca5c44df68fcac93` (SWIR) and `66de66eb6381dfdc856a` (TIR). This
environment used scikit-learn 1.9.1, NumPy 2.5.3, pandas 3.0.6. The runner
refuses a version mismatch with those model cards.

Remaining gates: acquire and benchmark 50 then 200 compatible holes; obtain
assay/lithology and independent geological review; verify redistribution terms;
perform a clean-machine install, saved screenshots and the five-question human
comprehension check. Technical details and measured limits are in
`EXECUTION_STATUS.md` and `EVIDENCE_2026-09-25.md`.

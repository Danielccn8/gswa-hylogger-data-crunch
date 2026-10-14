"""Background worker: claims queued jobs per lane and runs them.

    python -m hylogger.jobs.worker --lanes processed:4,raw:2,analysis:1

Acquisition steps run in child processes (hylogger.jobs.step) with a timeout
and are killed on cancellation; partial downloads resume on the next attempt.
Preparation and analysis run in-process. One PostgreSQL advisory lock per hole
stops two jobs writing the same hole at once.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..config import AppConfig
from ..explorer.db import connect, database_url
from ..runtime import command, hidden, root
from . import queue

STEP_TIMEOUTS = {"processed-fetch": 900, "raw-fetch": 4 * 3600, "raw-summarize": 1800}
# Spectra-only members are ~14% of a package; a fetch past this has stalled on the server.
SPECTRAL_FETCH_TIMEOUT = 3600
MIN_FREE_BYTES = 5 * 1024**3


class Cancelled(Exception):
    pass


class Context:
    def __init__(self, job: dict, data_dir: Path) -> None:
        self.job = job
        self.job_id = str(job["job_id"])
        self.params = job["params"] or {}
        self.data_dir = data_dir
        self.conn = connect()
        self._lock = threading.Lock()

    def log(self, message: str, level: str = "info") -> None:
        with self._lock:
            queue.log(self.conn, self.job_id, message, level)

    def progress(self, **values: Any) -> None:
        with self._lock:
            queue.progress(self.conn, self.job_id, **values)

    def check_cancel(self) -> None:
        with self._lock:
            if queue.cancel_requested(self.conn, self.job_id):
                raise Cancelled()

    def close(self) -> None:
        self.conn.close()


def run_step(
    ctx: Context, step: str, hole_id: str, *extra: str, timeout: int | None = None
) -> dict:
    """Run one child step; heartbeat, honour cancellation, enforce the timeout."""
    limit = timeout or STEP_TIMEOUTS[step]
    argv = command("hylogger.jobs.step", step, hole_id, *extra)
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=os.environ.copy(),
        **hidden(),
    )
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.extend(process.stdout), daemon=True)
    reader.start()
    started = time.monotonic()
    last_beat = 0.0
    try:
        while process.poll() is None:
            time.sleep(1)
            elapsed = time.monotonic() - started
            if elapsed - last_beat > 20:
                ctx.check_cancel()
                with ctx._lock:
                    queue.heartbeat(ctx.conn, ctx.job_id)
                last_beat = elapsed
            if elapsed > limit:
                process.kill()
                return {"ok": False, "error": f"timeout after {limit} s"}
    except Cancelled:
        process.kill()
        raise
    reader.join(timeout=5)
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    return {"ok": False, "error": "".join(lines[-5:]).strip() or f"exit {process.returncode}"}


def hole_lock(conn: psycopg.Connection, hole_id: str) -> None:
    conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (hole_id,))


def hole_unlock(conn: psycopg.Connection, hole_id: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (hole_id,))


def set_status(conn: psycopg.Connection, hole_id: str, column: str, value: str) -> None:
    conn.execute(
        f"UPDATE explorer_hole SET {column}=%s, updated_at=now() WHERE hole_id=%s",
        (value, hole_id),
    )
    conn.commit()


def per_hole(ctx: Context, holes: list[str], parallel: int, work) -> dict:
    """Apply work(hole, conn) to each hole with bounded parallelism and progress."""
    total, done, results = len(holes), 0, {"ok": [], "failed": {}}
    ctx.progress(total=total, done=0, ok=0, failed=0)
    state_lock = threading.Lock()

    def one(hole_id: str) -> None:
        nonlocal done
        ctx.check_cancel()
        conn = connect()
        try:
            hole_lock(conn, hole_id)
            outcome = work(hole_id, conn)
        except Cancelled:
            raise
        except Exception as error:  # one hole never stops the batch
            outcome = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        finally:
            try:
                hole_unlock(conn, hole_id)
            finally:
                conn.close()
        with state_lock:
            done += 1
            if outcome.get("ok"):
                results["ok"].append(hole_id)
            else:
                results["failed"][hole_id] = str(outcome.get("error"))[:500]
                ctx.log(f"{hole_id}: {outcome.get('error')}", "warning")
            ctx.progress(
                done=done, ok=len(results["ok"]), failed=len(results["failed"]), current=hole_id
            )

    with ThreadPoolExecutor(max(1, parallel)) as pool:
        for future in [pool.submit(one, hole) for hole in holes]:
            future.result()
    return results


def _holes(ctx: Context) -> list[str]:
    holes = [str(h).strip() for h in ctx.params.get("holes", []) if str(h).strip()]
    if not holes:
        raise ValueError("No holes given")
    return list(dict.fromkeys(holes))


def queue_analysis(ctx: Context, params: dict) -> str | None:
    """Queue one analysis refresh unless one is already waiting (it will include these holes)."""
    waiting = ctx.conn.execute(
        "SELECT job_id FROM job WHERE kind='analysis_refresh' AND status='queued' LIMIT 1"
    ).fetchone()
    ctx.conn.commit()
    if waiting:
        return str(waiting[0])
    return queue.submit(ctx.conn, "analysis_refresh", params, parent_id=ctx.job_id)


# --------------------------------------------------------------------- kinds
def job_processed_fetch(ctx: Context) -> dict:
    from ..explorer.prepare import prepare_hole

    prepare = ctx.params.get("prepare", True)

    def work(hole_id: str, conn: psycopg.Connection) -> dict:
        if ctx.params.get("auto_sync") or ctx.params.get("skip_existing"):
            status = conn.execute(
                "SELECT processed_status FROM explorer_hole WHERE hole_id=%s", (hole_id,)
            ).fetchone()
            conn.commit()
            if status and status[0] in ("prepared", "no_logs", "no_usable_logs"):
                return {"ok": True, "skipped": status[0]}
        outcome = run_step(ctx, "processed-fetch", hole_id)
        if not outcome.get("ok"):
            error = str(outcome.get("error"))
            unpublished = "No logs" in error or "no datasets" in error
            set_status(conn, hole_id, "processed_status", "no_logs" if unpublished else "failed")
            return outcome
        set_status(conn, hole_id, "processed_status", "fetched")
        if prepare:
            prepared = prepare_hole(conn, ctx.data_dir, hole_id)
            return {"ok": True, "prepared": prepared}
        return outcome

    result = per_hole(ctx, _holes(ctx), int(ctx.params.get("parallel", 4)), work)
    if ctx.params.get("analyze") and result["ok"]:
        result["analysis_job"] = queue_analysis(ctx, {"mode": "retrain", "promote": True})
    return result


def job_prepare(ctx: Context) -> dict:
    from ..explorer.prepare import prepare_hole

    root = ctx.data_dir / "downloads" / "processed"
    if not ctx.params.get("holes"):
        # Holes downloaded outside the queue (e.g. a bulk script): record those NVCL lists
        # but that publish no scalar logs, then prepare everything that has a table.
        empty = [
            path.name
            for path in root.iterdir()
            if (path / "inventory.json").exists() and not any(path.glob("*/scalar_logs.parquet"))
        ]
        ctx.conn.execute(
            "UPDATE explorer_hole SET processed_status='no_logs' WHERE hole_id = ANY(%s)", (empty,)
        )
        ctx.conn.commit()
        ctx.log(f"{len(empty)} holes have an NVCL dataset but no public scalar logs")
    holes = ctx.params.get("holes") or sorted(
        path.name for path in root.iterdir() if any(path.glob("*/scalar_logs.parquet"))
    )
    ctx.params["holes"] = holes

    def work(hole_id: str, conn: psycopg.Connection) -> dict:
        return {"ok": True, "prepared": prepare_hole(conn, ctx.data_dir, hole_id)}

    return per_hole(ctx, _holes(ctx), int(ctx.params.get("parallel", 4)), work)


def job_raw_probe(ctx: Context) -> dict:
    from ..raw import probe_raw

    config = AppConfig.load(data_dir=ctx.data_dir)
    sizes: dict[str, Any] = {}

    def work(hole_id: str, conn: psycopg.Connection) -> dict:
        probe = probe_raw(config, hole_id)
        sizes[hole_id] = probe.get("size_bytes")
        return {"ok": True}

    result = per_hole(ctx, _holes(ctx), 4, work)
    result["sizes"] = sizes
    result["total_bytes"] = sum(v for v in sizes.values() if v)
    return result


def _register_raw(conn: psycopg.Connection, data_dir: Path, hole_id: str, summary: dict) -> None:
    output = data_dir / "products" / "raw" / hole_id
    for sensor, item in summary.get("sensors", {}).items():
        if item.get("status") != "summarised":
            continue
        qc = item["qc"]
        conn.execute(
            """INSERT INTO raw_product(hole_id,sensor,dataset_id,product_path,n_intervals,
               depth_from_m,depth_to_m,summary,source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (hole_id,sensor) DO UPDATE SET product_path=EXCLUDED.product_path,
               n_intervals=EXCLUDED.n_intervals, depth_from_m=EXCLUDED.depth_from_m,
               depth_to_m=EXCLUDED.depth_to_m, summary=EXCLUDED.summary, source=EXCLUDED.source,
               created_at=now()""",
            (
                hole_id,
                sensor,
                Path(summary["package"]).name,
                str(output),
                qc["intervals"],
                item["depth_from_m"],
                item["depth_to_m"],
                Jsonb(item),
                Jsonb(
                    {
                        "package": summary["package"],
                        "discarded": "discarded_source_bytes" in summary,
                    }
                ),
            ),
        )
    conn.commit()


def job_raw_fetch(ctx: Context) -> dict:
    mode = ctx.params.get("mode", "spectral_only")
    summarize = ctx.params.get("summarize", True)
    discard = bool(ctx.params.get("discard_source", False))
    sizes = dict(
        ctx.conn.execute(
            "SELECT hole_id, coalesce(raw_size_bytes,0) FROM explorer_hole WHERE hole_id = ANY(%s)",
            (_holes(ctx),),
        )
        .fetchall()
    )

    force = bool(ctx.params.get("force", False))
    done_already = {
        row[0]
        for row in ctx.conn.execute(
            "SELECT hole_id FROM explorer_hole "
            "WHERE hole_id = ANY(%s) AND raw_status LIKE 'summarised%%'",
            (_holes(ctx),),
        ).fetchall()
    }
    ctx.conn.commit()

    def work(hole_id: str, conn: psycopg.Connection) -> dict:
        if hole_id in done_already and not force:
            return {"ok": True, "skipped": "already summarised"}
        if not force:
            summary_path = ctx.data_dir / "products" / "raw" / hole_id / "summary.json"
            if summary_path.exists():
                from ..errors import HyLoggerError
                from ..explorer.raw_summary import validate_saved_summary

                try:
                    saved = json.loads(summary_path.read_text(encoding="utf-8"))
                    if saved.get("validated"):
                        validate_saved_summary(summary_path.parent, saved)
                        _register_raw(conn, ctx.data_dir, hole_id, saved)
                        set_status(
                            conn,
                            hole_id,
                            "raw_status",
                            "summarised_discarded"
                            if saved.get("discard_requested")
                            else "summarised",
                        )
                        return {"ok": True, "recovered": "validated saved products"}
                except (HyLoggerError, ValueError, OSError, KeyError):
                    pass  # Incomplete products are fetched and verified again.
        need = sizes.get(hole_id, 0) * (0.6 if mode == "spectral_only" else 1.4)
        free = shutil.disk_usage(ctx.data_dir).free
        if free - need < MIN_FREE_BYTES:
            set_status(conn, hole_id, "raw_status", "failed")
            return {"ok": False, "error": f"Insufficient disk space ({free / 1e9:.1f} GB free)"}
        set_status(conn, hole_id, "raw_status", "downloading")
        timeout = SPECTRAL_FETCH_TIMEOUT if mode == "spectral_only" else None
        outcome = run_step(ctx, "raw-fetch", hole_id, "--mode", mode, timeout=timeout)
        if not outcome.get("ok"):
            set_status(conn, hole_id, "raw_status", "failed")
            return outcome
        set_status(conn, hole_id, "raw_status", "fetched")
        if not summarize:
            return outcome
        extra = ["--discard-source"] if discard else []
        summary = run_step(ctx, "raw-summarize", hole_id, *extra)
        if not summary.get("ok"):
            set_status(conn, hole_id, "raw_status", "summary_failed")
            return summary
        _register_raw(conn, ctx.data_dir, hole_id, summary["result"])
        set_status(conn, hole_id, "raw_status", "summarised_discarded" if discard else "summarised")
        return {"ok": True}

    result = per_hole(ctx, _holes(ctx), int(ctx.params.get("parallel", 2)), work)
    if ctx.params.get("analyze") and result["ok"]:
        result["analysis_job"] = queue_analysis(
            ctx, {"mode": "retrain", "promote": True, "raw": True}
        )
    return result


def job_raw_summarize(ctx: Context) -> dict:
    discard = bool(ctx.params.get("discard_source", False))

    def work(hole_id: str, conn: psycopg.Connection) -> dict:
        extra = ["--discard-source"] if discard else []
        summary = run_step(ctx, "raw-summarize", hole_id, *extra)
        if summary.get("ok"):
            _register_raw(conn, ctx.data_dir, hole_id, summary["result"])
            set_status(
                conn, hole_id, "raw_status", "summarised_discarded" if discard else "summarised"
            )
        return summary

    return per_hole(ctx, _holes(ctx), int(ctx.params.get("parallel", 2)), work)


def job_reference_load(ctx: Context) -> dict:
    import pandas as pd

    from ..explorer import reference

    sqlite_path = Path(
        os.environ.get(
            "HYLOGGER_REFERENCE_SQLITE",
            ctx.data_dir / "reference" / "HyLogger_Dataset_Package.sqlite",
        )
    )
    counts = reference.load_tables(ctx.conn, sqlite_path)
    catalog = pd.read_parquet(ctx.data_dir / "catalog" / "catalog.parquet")
    return {"tables": counts, "register": reference.build_register(ctx.conn, catalog)}


def job_catalog_refresh(ctx: Context) -> dict:
    import pandas as pd

    from ..catalog import refresh_catalog
    from ..explorer import reference

    result = refresh_catalog(AppConfig.load(data_dir=ctx.data_dir))
    catalog = pd.read_parquet(ctx.data_dir / "catalog" / "catalog.parquet")
    result["register"] = reference.build_register(ctx.conn, catalog)
    return result


def job_analysis_refresh(ctx: Context) -> dict:
    from ..explorer.analysis import run_analysis

    return run_analysis(
        ctx.conn,
        ctx.data_dir,
        mode=ctx.params.get("mode", "retrain"),
        promote=ctx.params.get("promote", True),
        raw=ctx.params.get("raw", True),
        log=ctx.log,
        progress=ctx.progress,
    )


def job_robustness_check(ctx: Context) -> dict:
    from ..explorer.analysis import run_robustness

    return run_robustness(ctx.conn, ctx.data_dir, log=ctx.log)


def job_promote(ctx: Context) -> dict:
    from ..explorer.analysis import promote

    return {"promoted": [promote(ctx.conn, run_id) for run_id in ctx.params.get("run_ids", [])]}


KINDS = {
    "processed_fetch": job_processed_fetch,
    "prepare": job_prepare,
    "raw_probe": job_raw_probe,
    "raw_fetch": job_raw_fetch,
    "raw_summarize": job_raw_summarize,
    "reference_load": job_reference_load,
    "catalog_refresh": job_catalog_refresh,
    "analysis_refresh": job_analysis_refresh,
    "robustness_check": job_robustness_check,
    "promote": job_promote,
}


def _beat(job_id: str, done: threading.Event) -> None:
    """Keep the job's heartbeat fresh while it runs, so only dead workers are requeued."""
    with connect() as conn:
        while not done.wait(30):
            queue.heartbeat(conn, job_id)


def run_job(job: dict, data_dir: Path) -> None:
    ctx = Context(job, data_dir)
    done = threading.Event()
    threading.Thread(target=_beat, args=(ctx.job_id, done), daemon=True).start()
    try:
        ctx.log(f"Started {job['kind']} on {socket.gethostname()}")
        result = KINDS[job["kind"]](ctx)
        failed = isinstance(result, dict) and result.get("failed") and not result.get("ok")
        queue.finish(ctx.conn, ctx.job_id, "failed" if failed else "succeeded", result=result)
    except Cancelled:
        queue.finish(ctx.conn, ctx.job_id, "cancelled", error="Cancelled by user")
    except Exception as error:
        ctx.log(traceback.format_exc(), "error")
        queue.finish(ctx.conn, ctx.job_id, "failed", error=f"{type(error).__name__}: {error}")
    finally:
        done.set()
        ctx.close()


def lane_loop(lane: str, slot: int, data_dir: Path, stop: threading.Event) -> None:
    name = f"{socket.gethostname()}:{os.getpid()}:{lane}{slot}"
    while not stop.is_set():
        try:
            with connect() as conn:
                job = queue.claim(conn, lane, name)
            if job is None:
                stop.wait(2)
                continue
            run_job(job, data_dir)
        except psycopg.OperationalError:
            stop.wait(5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lanes", default="processed:4,raw:2,analysis:1")
    args = parser.parse_args()
    sys.path.insert(0, str(root() / "platform"))
    from db.migrate import migrate

    for _attempt in range(30):
        try:
            applied = migrate(database_url())
            break
        except psycopg.OperationalError:
            time.sleep(2)
    else:
        raise SystemExit("Database unavailable")
    config = AppConfig.load()
    with connect() as conn:
        requeued = queue.requeue_stale(conn)
    print(
        f"worker ready: data={config.data_dir} migrations={applied} requeued={requeued}", flush=True
    )
    stop = threading.Event()
    threads = []
    for spec in args.lanes.split(","):
        lane, _, count = spec.partition(":")
        for slot in range(int(count or 1)):
            thread = threading.Thread(
                target=lane_loop, args=(lane, slot, config.data_dir, stop), daemon=True
            )
            thread.start()
            threads.append(thread)
    name = f"{socket.gethostname()}:{os.getpid()}"

    def presence(conn: psycopg.Connection) -> None:
        conn.execute(
            "INSERT INTO worker(name, lanes, host) VALUES (%s,%s,%s) "
            "ON CONFLICT (name) DO UPDATE SET seen_at=now(), lanes=EXCLUDED.lanes",
            (name, args.lanes, socket.gethostname()),
        )
        conn.commit()

    with connect() as conn:
        presence(conn)
    from .autofill import tick

    try:
        last_presence = 0.0
        while True:
            time.sleep(3)
            with connect() as conn:
                if time.monotonic() - last_presence > 30:
                    presence(conn)
                    queue.requeue_stale(conn)
                    last_presence = time.monotonic()
                try:
                    tick(conn)
                except Exception:
                    conn.rollback()
                    traceback.print_exc()
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()

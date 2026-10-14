"""Persistent public-data acquisition coordinator. Called by the ordinary worker.

Each hole/source has its own checkpoint. At most one automatic job per source is
in flight, with three attempts and backoff. Manual jobs remain independent.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import queue

TERMINAL = ("succeeded", "failed", "cancelled")


def sync_inventory(conn) -> None:
    for source, available, column in (
        ("processed", "nvcl_available", "processed_status"),
        ("raw", "raw_available", "raw_status"),
    ):
        conn.execute(
            f"INSERT INTO autofill_item(hole_id,source) SELECT hole_id,%s FROM explorer_hole "
            f"WHERE {available} ON CONFLICT DO NOTHING",
            (source,),
        )
        conn.execute(
            f"""UPDATE autofill_item a SET state=CASE
                WHEN h.{column} IN ('no_logs','no_usable_logs') THEN 'unavailable'
                ELSE 'complete' END, error=NULL, updated_at=now()
                FROM explorer_hole h WHERE h.hole_id=a.hole_id AND a.source=%s
                AND a.state <> 'running'
                AND (h.{column}='prepared' OR h.{column} LIKE 'summarised%%'
                     OR h.{column} IN ('no_logs','no_usable_logs'))""",
            (source,),
        )


def set_enabled(conn, enabled: bool, retry: bool = False) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(7107)")
    conn.execute(
        "UPDATE autofill_control SET enabled=%s, updated_at=now(), message=%s WHERE id=1",
        (enabled, "Discovering and acquiring public data" if enabled else "Paused"),
    )
    if retry:
        conn.execute(
            "UPDATE autofill_item SET state='pending', attempts=0, retry_at=NULL, "
            "error=NULL WHERE state IN ('failed','partial')"
        )
        conn.execute("UPDATE autofill_control SET catalog_checked_at=NULL WHERE id=1")
    if not enabled:
        conn.execute(
            "UPDATE job SET status=CASE WHEN status='queued' THEN 'cancelled' "
            "ELSE 'cancel_requested' END WHERE params->>'auto_sync'='true' "
            "AND status IN ('queued','running')"
        )
    conn.commit()


def report(conn) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM autofill_control WHERE id=1")
        control = cur.fetchone()
        cur.execute("SELECT source,state,count(*) AS n FROM autofill_item GROUP BY source,state")
        counts = {
            s: dict(total=0, complete=0, pending=0, running=0, failed=0, partial=0, unavailable=0)
            for s in ("processed", "raw")
        }
        for row in cur.fetchall():
            counts[row["source"]][row["state"]] = row["n"]
            counts[row["source"]]["total"] += row["n"]
        cur.execute(
            "SELECT hole_id,source,state,attempts,error,retry_at FROM autofill_item "
            "WHERE state IN ('failed','partial') ORDER BY updated_at DESC LIMIT 20"
        )
        problems = cur.fetchall()
    return {
        "control": control,
        "sources": counts,
        "problems": problems,
        "scope": "Public Western Australia NVCL / NCI THREDDS catalogue",
        "retention": "QC-admitted 1 m median spectra, wavelengths, depth bins and TSA summaries",
    }


def tick(conn) -> None:
    # Multiple workers may be started; only one coordinator schedules jobs.
    if not conn.execute("SELECT pg_try_advisory_lock(7107)").fetchone()[0]:
        conn.rollback()
        return
    try:
        _tick(conn)
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(7107)")
        conn.commit()


def _tick(conn) -> None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM autofill_control WHERE id=1")
        control = cur.fetchone()
        cur.execute(
            "SELECT a.*, j.status AS job_status,j.result,j.error AS job_error, "
            "h.processed_status,h.raw_status FROM autofill_item a "
            "JOIN job j USING(job_id) JOIN explorer_hole h USING(hole_id) "
            "WHERE a.state='running' AND j.status=ANY(%s)",
            (list(TERMINAL),),
        )
        finished = cur.fetchall()
    for item in finished:
        status = item[f"{item['source']}_status"]
        failure = (item["result"] or {}).get("failed", {}).get(item["hole_id"])
        if item["job_status"] == "cancelled":
            state = "pending"
        elif item["job_status"] == "failed" or failure:
            state = "failed"
        elif status in ("no_logs", "no_usable_logs"):
            state = "unavailable"
        elif status == "prepared" or status.startswith("summarised"):
            state = "complete"
        else:
            state = "partial"
        error = failure or item["job_error"]
        if state == "partial":
            error = f"Processing incomplete ({status}); source retained"
        conn.execute(
            "UPDATE autofill_item SET state=%s,error=%s,job_id=NULL, "
            "attempts=attempts-%s,retry_at=now()+make_interval(mins => 5*attempts*attempts), "
            "updated_at=now() WHERE hole_id=%s AND source=%s",
            (state, error, int(state == "pending"), item["hole_id"], item["source"]),
        )
        if state == "complete":
            conn.execute("UPDATE autofill_control SET dirty_count=dirty_count+1 WHERE id=1")
    sync_inventory(conn)
    conn.commit()
    if not control["enabled"]:
        return
    if control["catalog_job"]:
        job = conn.execute(
            "SELECT status,error,result FROM job WHERE job_id=%s", (control["catalog_job"],)
        ).fetchone()
        if job and job[0] not in TERMINAL:
            conn.commit()
            return
        warnings = (job[2] or {}).get("warnings") if job else None
        message = job[1] if job and job[1] else "; ".join(warnings or [])
        conn.execute(
            "UPDATE autofill_control SET catalog_job=NULL,message=%s WHERE id=1",
            (message or "Catalogue ready; acquiring pending holes",),
        )
        conn.commit()
    refresh = conn.execute(
        "SELECT catalog_checked_at IS NULL OR "
        "catalog_checked_at < now()-interval '1 day' FROM autofill_control "
        "WHERE id=1"
    ).fetchone()[0]
    if refresh:
        job_id = queue.submit(conn, "catalog_refresh", {"auto_sync": True})
        conn.execute(
            "UPDATE autofill_control SET catalog_job=%s,catalog_checked_at=now() WHERE id=1",
            (job_id,),
        )
        conn.commit()
        return
    for source, kind in (("processed", "processed_fetch"), ("raw", "raw_fetch")):
        busy = conn.execute(
            "SELECT 1 FROM job WHERE kind=%s AND status=ANY(%s) LIMIT 1", (kind, list(queue.ACTIVE))
        ).fetchone()
        if busy:
            continue
        item = conn.execute(
            "SELECT hole_id FROM autofill_item WHERE source=%s "
            "AND (state='pending' OR (state IN ('failed','partial') "
            "AND attempts<3 AND retry_at<=now())) "
            "ORDER BY attempts,hole_id LIMIT 1",
            (source,),
        ).fetchone()
        if not item:
            continue
        params = {
            "holes": [item[0]],
            "auto_sync": True,
            "parallel": 1,
            "analyze": False,
            "mode": "spectral_only",
            "discard_source": True,
        }
        # Job and checkpoint are committed together, including across a power loss.
        job_id = conn.execute(
            "INSERT INTO job(kind,lane,params) VALUES (%s,%s,%s) RETURNING job_id",
            (kind, queue.LANES[kind], Jsonb(params)),
        ).fetchone()[0]
        conn.execute(
            "UPDATE autofill_item SET state='running',attempts=attempts+1,job_id=%s, "
            "updated_at=now() WHERE hole_id=%s AND source=%s",
            (job_id, item[0], source),
        )
        conn.commit()
    dirty = conn.execute("SELECT dirty_count FROM autofill_control WHERE id=1").fetchone()[0]
    remaining = conn.execute(
        "SELECT count(*) FROM autofill_item WHERE state IN ('pending','running')"
    ).fetchone()[0]
    if dirty and (dirty >= 20 or not remaining):
        busy = conn.execute(
            "SELECT 1 FROM job WHERE lane='analysis' AND status=ANY(%s) LIMIT 1",
            (list(queue.ACTIVE),),
        ).fetchone()
        if not busy:
            job_id = queue.submit(
                conn,
                "analysis_refresh",
                {"mode": "retrain", "promote": True, "raw": True, "auto_sync": True},
            )
            conn.execute(
                "UPDATE autofill_control SET analysis_job=%s,dirty_count=0 WHERE id=1", (job_id,)
            )
    conn.commit()

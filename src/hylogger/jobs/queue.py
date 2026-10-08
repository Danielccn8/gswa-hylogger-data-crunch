"""Job queue on PostgreSQL: submit, claim (SKIP LOCKED), progress, logs and cancellation."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

LANES = {
    "catalog_refresh": "processed",
    "reference_load": "analysis",
    "processed_fetch": "processed",
    "prepare": "analysis",
    "raw_probe": "raw",
    "raw_fetch": "raw",
    "raw_summarize": "analysis",
    "analysis_refresh": "analysis",
    "robustness_check": "analysis",
    "promote": "analysis",
}
ACTIVE = ("queued", "running", "cancel_requested")
STALE_AFTER = "5 minutes"


def submit(
    conn: psycopg.Connection,
    kind: str,
    params: dict[str, Any] | None = None,
    *,
    priority: int = 100,
    parent_id: str | None = None,
) -> str:
    if kind not in LANES:
        raise ValueError(f"Unknown job kind: {kind}")
    row = conn.execute(
        "INSERT INTO job(kind,lane,params,priority,parent_id) VALUES (%s,%s,%s,%s,%s) "
        "RETURNING job_id",
        (kind, LANES[kind], Jsonb(params or {}), priority, parent_id),
    ).fetchone()
    conn.commit()
    return str(row[0])


def claim(conn: psycopg.Connection, lane: str, worker: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """UPDATE job SET status='running', started_at=now(), heartbeat_at=now(),
                   worker=%s, attempts=attempts+1
               WHERE job_id = (
                   SELECT job_id FROM job WHERE lane=%s AND status='queued'
                   ORDER BY priority, created_at FOR UPDATE SKIP LOCKED LIMIT 1)
               RETURNING *""",
            (worker, lane),
        )
        job = cur.fetchone()
    conn.commit()
    return job


def requeue_stale(conn: psycopg.Connection) -> int:
    """Return interrupted jobs to the queue; acquisition steps resume safely."""
    count = conn.execute(
        f"""UPDATE job SET status=CASE WHEN status='cancel_requested' THEN 'cancelled'
                ELSE 'queued' END, worker=NULL
            WHERE status IN ('running','cancel_requested')
              AND heartbeat_at < now() - interval '{STALE_AFTER}'"""
    ).rowcount
    conn.commit()
    return count


def heartbeat(conn: psycopg.Connection, job_id: str) -> None:
    conn.execute("UPDATE job SET heartbeat_at=now() WHERE job_id=%s", (job_id,))
    conn.commit()


def progress(conn: psycopg.Connection, job_id: str, **values: Any) -> None:
    conn.execute(
        "UPDATE job SET progress = progress || %s, heartbeat_at=now() WHERE job_id=%s",
        (Jsonb(values), job_id),
    )
    conn.commit()


def log(conn: psycopg.Connection, job_id: str, message: str, level: str = "info") -> None:
    conn.execute(
        "INSERT INTO job_log(job_id, level, message) VALUES (%s,%s,%s)",
        (job_id, level, message[:4000]),
    )
    conn.commit()


def cancel_requested(conn: psycopg.Connection, job_id: str) -> bool:
    row = conn.execute("SELECT status FROM job WHERE job_id=%s", (job_id,)).fetchone()
    conn.commit()
    return row is not None and row[0] == "cancel_requested"


def request_cancel(conn: psycopg.Connection, job_id: str) -> str | None:
    row = conn.execute(
        """UPDATE job SET status=CASE WHEN status='queued' THEN 'cancelled'
               ELSE 'cancel_requested' END,
               finished_at=CASE WHEN status='queued' THEN now() ELSE finished_at END
           WHERE job_id=%s AND status IN ('queued','running') RETURNING status""",
        (job_id,),
    ).fetchone()
    conn.commit()
    return row[0] if row else None


def finish(
    conn: psycopg.Connection,
    job_id: str,
    status: str,
    *,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE job SET status=%s, result=%s, error=%s, finished_at=now(), heartbeat_at=now() "
        "WHERE job_id=%s",
        (status, Jsonb(result) if result is not None else None, error, job_id),
    )
    conn.commit()

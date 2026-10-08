from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from hylogger.jobs import queue

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn():
    url = os.environ.get("HYLOGGER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("HYLOGGER_TEST_DATABASE_URL is not set")
    connection = psycopg.connect(url)
    schema = f"test_jobs_{uuid.uuid4().hex[:8]}"
    connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"SET search_path TO {schema}, public")
    connection.execute((ROOT / "platform/db/migrations/004_jobs.sql").read_text(encoding="utf-8"))
    connection.commit()
    yield connection
    connection.rollback()
    connection.execute(f"DROP SCHEMA {schema} CASCADE")
    connection.commit()
    connection.close()


def test_submit_claim_progress_finish(conn):
    job_id = queue.submit(conn, "processed_fetch", {"holes": ["A", "B"]})
    assert queue.claim(conn, "raw", "w1") is None  # wrong lane
    job = queue.claim(conn, "processed", "w1")
    assert str(job["job_id"]) == job_id and job["status"] == "running" and job["attempts"] == 1
    assert queue.claim(conn, "processed", "w2") is None  # already claimed
    queue.progress(conn, job_id, done=1, total=2)
    queue.log(conn, job_id, "half way")
    queue.finish(conn, job_id, "succeeded", result={"ok": ["A", "B"]})
    status, progress = conn.execute(
        "SELECT status, progress FROM job WHERE job_id=%s", (job_id,)
    ).fetchone()
    assert status == "succeeded" and progress == {"done": 1, "total": 2}


def test_cancel_queued_and_running(conn):
    queued = queue.submit(conn, "raw_fetch", {"holes": ["A"]})
    assert queue.request_cancel(conn, queued) == "cancelled"
    running = queue.submit(conn, "raw_fetch", {"holes": ["B"]})
    queue.claim(conn, "raw", "w1")
    assert queue.request_cancel(conn, running) == "cancel_requested"
    assert queue.cancel_requested(conn, running)


def test_stale_running_job_is_requeued(conn):
    job_id = queue.submit(conn, "prepare", {})
    queue.claim(conn, "analysis", "dead-worker")
    conn.execute(
        "UPDATE job SET heartbeat_at = now() - interval '1 hour' WHERE job_id=%s", (job_id,)
    )
    conn.commit()
    assert queue.requeue_stale(conn) == 1
    assert (
        conn.execute("SELECT status FROM job WHERE job_id=%s", (job_id,)).fetchone()[0] == "queued"
    )


def test_unknown_kind_rejected(conn):
    with pytest.raises(ValueError):
        queue.submit(conn, "format_disk", {})

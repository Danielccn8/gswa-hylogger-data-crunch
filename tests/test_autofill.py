"""Acceptance of durable acquisition in an isolated schema, without network calls."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from psycopg.types.json import Jsonb

from hylogger.explorer.reference import build_register
from hylogger.jobs.autofill import report, set_enabled, tick

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn():
    url = os.environ.get("HYLOGGER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated test database not configured")
    conn = psycopg.connect(url)
    schema = "test_auto_" + uuid.uuid4().hex[:10]
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"SET search_path TO {schema},public")
    for name in ("004_jobs.sql", "005_explorer.sql", "006_autofill.sql"):
        conn.execute((ROOT / "platform/db/migrations" / name).read_text())
    conn.execute("UPDATE autofill_control SET catalog_checked_at=now()")
    conn.commit()
    yield conn
    conn.rollback()
    conn.execute("SET search_path TO public")
    conn.execute(f"DROP SCHEMA {schema} CASCADE")
    conn.commit()
    conn.close()


def seed(conn):
    conn.execute(
        "INSERT INTO explorer_hole(hole_id,nvcl_available,raw_available) "
        "VALUES ('A',true,true),('B',true,false)"
    )
    conn.commit()


def test_public_bootstrap_preserves_completed_products(conn):
    catalog = pd.DataFrame(
        [
            dict(
                hole_id="PUBLIC",
                lat=-30.0,
                lon=120.0,
                nvcl_available=True,
                raw_available=True,
                raw_size_bytes=100,
            )
        ]
    )
    assert build_register(conn, catalog)["holes"] == 1
    conn.execute("UPDATE explorer_hole SET processed_status='prepared'")
    conn.commit()
    build_register(conn, catalog)
    assert conn.execute("SELECT processed_status FROM explorer_hole").fetchone()[0] == "prepared"
    assert conn.execute("SELECT to_regclass('ref_index')").fetchone()[0] is None


def test_restart_dedup_pause_resume_and_no_logs(conn):
    seed(conn)
    set_enabled(conn, True)
    tick(conn)
    assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 2
    conn.commit()
    tick(conn)  # another iteration/restart cannot enqueue the same hole/source
    assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 2
    set_enabled(conn, False)
    tick(conn)
    assert report(conn)["sources"]["raw"]["pending"] == 1
    set_enabled(conn, True)
    tick(conn)
    conn.execute("UPDATE explorer_hole SET processed_status='no_logs' WHERE hole_id='A'")
    conn.execute(
        "UPDATE job SET status='succeeded',result=%s WHERE status='queued' "
        "AND kind='processed_fetch'",
        (Jsonb({"ok": ["A"]}),),
    )
    conn.commit()
    tick(conn)
    assert report(conn)["sources"]["processed"]["unavailable"] == 1
    assert (
        conn.execute(
            "SELECT count(*) FROM job WHERE kind='processed_fetch' "
            "AND params->'holes'='[\"A\"]'::jsonb AND status='queued'"
        ).fetchone()[0]
        == 0
    )


def test_retry_backoff_and_limit(conn):
    seed(conn)
    conn.execute("DELETE FROM explorer_hole WHERE hole_id='B'")
    conn.commit()
    set_enabled(conn, True)
    for _attempt in range(3):
        tick(conn)
        conn.execute(
            "UPDATE job SET status='failed',error='provider unavailable' WHERE status='queued'"
        )
        conn.commit()
        tick(conn)
        # During backoff these holes cannot immediately be retried.
        assert (
            conn.execute(
                "SELECT count(*) FROM job WHERE status='queued' AND "
                "params->'holes'='[\"A\"]'::jsonb"
            ).fetchone()[0]
            == 0
        )
        conn.execute("UPDATE autofill_item SET retry_at=now()-interval '1 minute'")
        conn.commit()
    tick(conn)
    assert (
        conn.execute(
            "SELECT attempts FROM autofill_item WHERE hole_id='A' AND source='raw'"
        ).fetchone()[0]
        == 3
    )
    set_enabled(conn, True, retry=True)
    assert (
        conn.execute(
            "SELECT attempts FROM autofill_item WHERE hole_id='A' AND source='raw'"
        ).fetchone()[0]
        == 0
    )


def test_completed_skipped_and_analysis_coalesced(conn):
    seed(conn)
    conn.execute(
        "UPDATE explorer_hole SET processed_status='prepared',raw_status='summarised' "
        "WHERE hole_id='A'"
    )
    conn.execute("UPDATE explorer_hole SET processed_status='no_logs' WHERE hole_id='B'")
    conn.execute("UPDATE autofill_control SET dirty_count=1")
    conn.commit()
    set_enabled(conn, True)
    tick(conn)
    tick(conn)
    assert conn.execute("SELECT kind FROM job").fetchall() == [("analysis_refresh",)]
    assert report(conn)["sources"]["raw"]["complete"] == 1

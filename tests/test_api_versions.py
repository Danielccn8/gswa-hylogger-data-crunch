"""Optional integration checks in a rolled-back schema of a local test database.

Set HYLOGGER_TEST_DATABASE_URL to run. No existing table is read or modified.
"""

import importlib.util
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def api(monkeypatch):
    url = os.environ.get("HYLOGGER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Local database integration requires HYLOGGER_TEST_DATABASE_URL")
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "delivery_api_test", root / "platform/backend/legacy.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with psycopg.connect(url, connect_timeout=5, row_factory=dict_row) as conn:
        schema = "test_delivery_" + uuid4().hex
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        for name in ("002_interval_system.sql", "003_active_interval_sets.sql"):
            conn.execute((root / "platform/db/migrations" / name).read_text())
        conn.execute(
            "INSERT INTO model_run(model_id,region,card) VALUES('M','swir',%s)",
            (Jsonb({"feature_schema": ["X", "Y"]}),),
        )
        conn.execute("INSERT INTO active_model VALUES('swir','M')")
        for set_id, hole, active, feature, cluster, centroid in (
            ("old", "A", False, {"X": 0.9, "Y": 0.1}, 0, 0),
            ("new", "A", True, {"X": 0.9, "Y": 0.1}, 0, 0.3),
            ("near", "B", True, {"X": 0.9, "Y": 0.1}, 1, 0.9),
            ("far", "C", True, {"X": 0.1, "Y": 0.9}, 0, 0.01),
        ):
            conn.execute(
                "INSERT INTO etl_run(interval_set_id,hole_id,dataset_id,region,source_sha256,qc) "
                "VALUES(%s,%s,'D','swir','hash',%s)",
                (set_id, hole, Jsonb({"mask_status": "unavailable_in_processed_log"})),
            )
            conn.execute(
                """INSERT INTO interval_product
                (interval_id,interval_set_id,hole_id,dataset_id,region,
                depth_from_m,depth_to_m,n_total,n_admitted,source_rows,features,reason_counts)
                VALUES(%s,%s,%s,'D','swir',1,2,10,10,'[0,1]',%s,'{"admitted":10}')""",
                (set_id, set_id, hole, Jsonb(feature)),
            )
            conn.execute(
                "INSERT INTO interval_assignment VALUES('M',%s,%s,%s,'assigned')",
                (set_id, cluster, centroid),
            )
            if active:
                conn.execute(
                    "INSERT INTO active_interval_set(hole_id,region,interval_set_id) "
                    "VALUES(%s,'swir',%s)",
                    (hole, set_id),
                )

        @contextmanager
        def isolated_db():
            yield conn

        monkeypatch.setattr(module, "db", isolated_db)
        try:
            yield module
        finally:
            conn.rollback()


def test_profile_selects_one_revision_and_history_is_explicit(api):
    selected = api.system_intervals("A", "swir", None, None, None, 10, None, 0)
    assert [i["interval_id"] for i in selected["intervals"]] == ["new"]
    historical = api.system_intervals("A", "swir", None, None, None, 10, "old", 0)
    assert [i["interval_id"] for i in historical["intervals"]] == ["old"]
    assert api.system_coverage()[0]["intervals"] == 1


def test_similarity_uses_query_distance_across_groups_excluding_history(api):
    result = api.system_similar("new", None, 10)
    assert [i["interval_id"] for i in result["items"]] == ["near", "far"]
    assert result["items"][0]["distance"] == 0
    assert result["items"][1]["distance"] > 0.5


def test_export_matches_selected_revision_and_model(api):
    import csv
    import io

    response = api.system_export("A", "swir", "M", 1, 2, "new")
    rows = list(csv.DictReader(io.StringIO(response.body.decode())))
    assert len(rows) == 1 and rows[0]["interval_set_id"] == "new"
    assert rows[0]["model_id"] == "M"


def test_empty_and_reversed_depth_ranges_are_explicit(api):
    from fastapi import HTTPException

    result = api.system_intervals("A", "swir", None, 50, 60, 10, None, 0)
    assert result["intervals"] == []
    with pytest.raises(HTTPException) as error:
        api.system_intervals("A", "swir", None, 2, 1, 10, None, 0)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        api.system_intervals("A", "swir", None, float("nan"), None, 10, None, 0)

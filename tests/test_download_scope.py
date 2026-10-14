from __future__ import annotations

import ast
import shutil
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Body, HTTPException
from psycopg.types.json import Jsonb

from hylogger.jobs.queue import LANES


def api_functions(rows, tmp_path):
    inserted, reads = [], []

    def query(sql, params=None):
        reads.append(sql)
        assert "LIMIT" not in sql
        return rows if params is None else [r for r in rows if r["hole_id"] in params[0]]

    def execute(sql, params):
        assert sql.startswith("INSERT INTO job")
        inserted.append(params)
        return SimpleNamespace(fetchone=lambda: {"job_id": uuid4(), "params": params[2].obj})

    namespace = {
        "query": query,
        "MAX_JOB_HOLES": 2500,
        "HTTPException": HTTPException,
        "Annotated": Annotated,
        "Body": Body,
        "shutil": shutil,
        "DATA_DIR": tmp_path,
        "LANES": LANES,
        "Jsonb": Jsonb,
        "datetime": datetime,
        "UTC": UTC,
        "clean": lambda value: value,
        "pool": SimpleNamespace(connection=lambda: nullcontext(SimpleNamespace(execute=execute))),
    }
    path = Path(__file__).parents[1] / "platform/backend/main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"download_scope_rows", "acquisition_rows", "estimate", "_job_row", "create_job"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace, inserted, reads


def hole(index, processed="not_fetched", raw="not_fetched", nvcl=True, package=True):
    return {
        "hole_id": f"H{index:04}",
        "nvcl_available": nvcl,
        "raw_available": package,
        "size": 100_000_000,
        "processed_status": processed,
        "raw_status": raw,
    }


def test_all_catalogue_resolves_more_than_display_and_selected_limit(tmp_path):
    rows = [hole(i) for i in range(3100)] + [
        hole(3100, processed="prepared"),
        hole(3101, processed="no_logs"),
    ]
    api, inserted, reads = api_functions(rows, tmp_path)
    estimate = api["estimate"]({"scope": "all_catalogue"})
    assert estimate["holes"] == 3102 and estimate["processed"]["download_holes"] == 3100
    assert estimate["processed"]["already_prepared"] == 1
    assert estimate["processed"]["no_public_logs"] == 1
    job = api["create_job"]({"kind": "processed_fetch", "params": {"scope": "all_catalogue"}})
    assert len(job["params"]["holes"]) == 3100
    assert job["params"]["holes"][-1] == "H3099"
    assert job["params"]["skip_existing"] is True
    assert job["params"]["catalogue_holes_at_submission"] == 3102
    assert "auto_sync" not in job["params"] and len(inserted) == 1 and reads


def test_selected_processed_retains_refetch_and_raw_reuses_summaries(tmp_path):
    rows = [
        hole(1, processed="prepared", raw="summarised_discarded"),
        hole(2),
        hole(3, nvcl=False, package=False),
    ]
    api, inserted, _ = api_functions(rows, tmp_path)
    estimate = api["estimate"]({"holes": ["H0001", "H0002", "H0003"]})
    assert estimate["processed"]["download_holes"] == 2
    assert estimate["raw"]["download_holes"] == 1
    assert estimate["raw"]["zip_bytes"] == 100_000_000
    job = api["create_job"]({"kind": "processed_fetch", "params": {"holes": ["H0001"]}})
    assert job["params"]["holes"] == ["H0001"] and "skip_existing" not in job["params"]
    raw = api["create_job"]({"kind": "raw_fetch", "params": {"holes": ["H0001", "H0002"]}})
    assert raw["params"]["holes"] == ["H0002"] and len(inserted) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "invalid"},
        {"holes": "H0001"},
        {"holes": [None]},
        {"holes": ["unknown"]},
        {"holes": [f"H{i:04}" for i in range(2501)]},
    ],
)
def test_invalid_selected_estimate_never_silently_truncates(tmp_path, payload):
    api, inserted, _ = api_functions([hole(1)], tmp_path)
    with pytest.raises(HTTPException) as error:
        api["estimate"](payload)
    assert error.value.status_code == 422 and not inserted


@pytest.mark.parametrize("holes", [["unknown"], [f"H{i:04}" for i in range(2501)]])
def test_non_fetch_cannot_bypass_hole_validation_with_all_scope(tmp_path, holes):
    api, inserted, _ = api_functions([hole(1)], tmp_path)
    with pytest.raises(HTTPException) as error:
        api["create_job"]({"kind": "prepare", "params": {"scope": "all_catalogue", "holes": holes}})
    assert error.value.status_code == 422 and not inserted


def test_empty_all_and_force_rejected_without_queue_write(tmp_path):
    api, inserted, _ = api_functions([hole(1, processed="prepared", raw="summarised")], tmp_path)
    assert api["estimate"]({"scope": "all_catalogue"})["processed"]["download_holes"] == 0
    for payload in [{"scope": "all_catalogue", "force": True}]:
        with pytest.raises(HTTPException):
            api["estimate"](payload)
    for kind in ["processed_fetch", "raw_fetch"]:
        with pytest.raises(HTTPException):
            api["create_job"]({"kind": kind, "params": {"scope": "all_catalogue"}})
        with pytest.raises(HTTPException):
            api["create_job"]({"kind": kind, "params": {"scope": "all_catalogue", "force": True}})
    assert not inserted


def test_processed_skip_existing_never_calls_remote_step(tmp_path, monkeypatch):
    from hylogger.jobs import worker

    conn = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(fetchone=lambda: ("prepared",)), commit=lambda: None
    )
    ctx = SimpleNamespace(params={"holes": ["H0001"], "skip_existing": True}, data_dir=tmp_path)
    monkeypatch.setattr(worker, "run_step", lambda *args: pytest.fail("remote acquisition"))
    monkeypatch.setattr(
        worker,
        "per_hole",
        lambda context, holes, parallel, work: {"ok": [holes[0]], "work": work(holes[0], conn)},
    )
    assert worker.job_processed_fetch(ctx)["work"]["skipped"] == "prepared"

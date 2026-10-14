from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hylogger.explorer import analysis
from hylogger.explorer.data import FrozenFeatureSchema, IntervalMatrix


def view(names, matrix):
    return {
        "names": names,
        "matrix": np.asarray(matrix, dtype=float),
        "summaries": {},
        "run_id": "source-" + "".join(names),
        "minerals": pd.DataFrame(index=names),
    }


def test_combined_requires_all_sensor_support_and_preserves_distance():
    views = {
        "swir": view(["a", "b", "c"], [[0, 0.2, 0.7], [0.2, 0, 0.4], [0.7, 0.4, 0]]),
        "tir": view(["c", "b", "d"], [[0, 0.8, 0.9], [0.8, 0, 0.3], [0.9, 0.3, 0]]),
    }
    names, matrix, coverage = analysis.comparable_combined_distance(views)
    assert names == ["b", "c"]
    assert np.allclose(matrix, [[0, 0.6], [0.6, 0]])
    assert coverage["union_holes"] == 4 and coverage["common_holes"] == 2
    assert coverage["excluded_hole_ids"] == ["a", "d"]
    assert coverage["pair_sensor_support"] == 2
    assert coverage["missing_distance_imputation"] == "none"


@pytest.mark.parametrize("names", [["b"], ["a", "b"]])
def test_empty_or_single_combined_support_skips_without_model(tmp_path, monkeypatch, names):
    other = ["b"] if names == ["b"] else ["c", "d"]
    views = {
        "swir": view(names, np.zeros((len(names), len(names)))),
        "tir": view(other, np.zeros((len(other), len(other)))),
    }
    monkeypatch.setattr(analysis, "hole_run", lambda *a, **k: pytest.fail("unsupported model"))
    logs = []
    assert analysis.combined_view(None, tmp_path, views, logs.append) is None
    assert "common supported holes" in logs[0]


@pytest.mark.parametrize(
    "matrix", [[[0, np.nan], [np.nan, 0]], [[0, 1], [0.2, 0]], [[0, -1], [-1, 0]]]
)
def test_invalid_sensor_distances_are_rejected(matrix):
    with pytest.raises(ValueError):
        analysis.comparable_combined_distance({"swir": view(["a", "b"], matrix)})


def test_interval_provenance_binds_values_rows_and_schema():
    data = IntervalMatrix(
        "swir",
        pd.DataFrame({"hole_id": ["a", "b"], "bin": [0, 1]}),
        ["A", "B"],
        np.array([[0.8, 0.2], [0.1, 0.9]], np.float32),
        {},
    )
    schema = FrozenFeatureSchema.fit(data)
    first = analysis.interval_provenance(data, schema)
    data.abundance[0] = [0.7, 0.3]
    second = analysis.interval_provenance(data, schema)
    assert first["data_sha256"] != second["data_sha256"]
    assert first["feature_schema_sha256"] == second["feature_schema_sha256"]
    assert "descriptive/transductive" in first["schema_fit_scope"]


def test_historical_robustness_is_retained_but_not_current():
    # Extract the pure gate without opening the API connection pool.
    path = Path(__file__).parents[1] / "platform/backend/main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "robustness_for_model"
    )
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    gate = namespace["robustness_for_model"]
    model = {"run_id": "new", "card": {"provenance": {"data_sha256": "abc"}}}
    historical = {"run_id": "old-report", "card": {"district_transfer": {"mean_ari": 0.9}}}
    current, status = gate(historical, model)
    assert current is None and status["historical_card"] == historical["card"]
    linked = {
        "run_id": "new-report",
        "card": {"source_interval_run": "new", "source_provenance": model["card"]["provenance"]},
    }
    assert gate(linked, model)[1]["status"] == "current"
    model["card"]["provenance"] = {"data_sha256": "different"}
    assert gate(linked, model)[0] is None


def test_manual_cut_has_exact_k_for_tied_heights_and_reports_singletons():
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform

    path = Path(__file__).parents[1] / "platform/backend/main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "manual_hole_grouping"
    )
    namespace = {"np": np}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    cut = namespace["manual_hole_grouping"]
    distance = np.ones((4, 4)) - np.eye(4)
    hierarchy = linkage(squareform(distance), method="average")
    labels, card = cut(hierarchy, 4, 3)
    assert len(np.unique(labels)) == card["actual_k"] == card["requested_k"] == 3
    assert card["supported"] is False and card["status"] == "exploratory_unsupported"
    separated = np.array([[0, 0.1, 1, 1], [0.1, 0, 1, 1], [1, 1, 0, 0.1], [1, 1, 0.1, 0]])
    labels, card = cut(linkage(squareform(separated), method="average"), 4, 2)
    assert card["supported"] and sorted(np.bincount(labels)) == [2, 2]
    with pytest.raises(ValueError):
        cut(hierarchy, 4, 4)


def test_unsupported_container_has_no_public_cluster_count():
    path = Path(__file__).parents[1] / "platform/backend/main.py"
    node = next(
        n
        for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, ast.FunctionDef) and n.name == "grouping_display"
    )
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    result = namespace["grouping_display"](
        {
            "hole_clustering": {
                "k": 1,
                "descriptive_container": True,
                "status": "no_valid_candidates",
            }
        }
    )
    assert result["k"] is None and result["supported"] is False
    assert "Unassigned" in result["meaning"]


def test_future_unsupported_hole_run_keeps_unassigned_name(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    captured = {}
    monkeypatch.setattr(
        analysis, "_store_run", lambda conn, run_id, kind, sensor, card, path: captured.update(card)
    )
    monkeypatch.setattr(analysis, "copy_frame", lambda *args, **kwargs: None)
    names = ["a", "b", "c"]
    analysis.hole_run(
        SimpleNamespace(transaction=nullcontext),
        tmp_path,
        "raw_nir",
        names,
        np.zeros((3, 3)),
        {},
        {"distance": "test"},
        naming=pd.DataFrame({"Quartz": [1.0, 1.0, 1.0]}, index=names),
    )
    assert captured["hole_clustering"]["descriptive_container"]
    assert captured["hole_clustering"]["names"] == {0: "Unassigned — no supported hole grouping"}

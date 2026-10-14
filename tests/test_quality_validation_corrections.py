from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from hylogger.explorer import data as data_module
from hylogger.explorer import robustness
from hylogger.explorer.data import (
    FrozenFeatureSchema,
    IntervalMatrix,
    SupportPolicy,
    support_eligibility,
)
from hylogger.explorer.features import interval_features


def matrix(abundance, holes=None, minerals=None):
    abundance = np.asarray(abundance, dtype=np.float32)
    n = len(abundance)
    meta = pd.DataFrame(
        {
            "hole_id": holes or ["H"] * n,
            "bin": np.arange(n),
            "feature_set_id": "F",
            "n_total": 10,
            "n_admitted": 8,
            "depth_from_m": np.arange(n),
            "depth_to_m": np.arange(n) + 1,
        }
    )
    return IntervalMatrix(
        "swir", meta, minerals or ["A", "B", "C"], abundance, {"A": "G", "B": "G", "C": "HELD_ONLY"}
    )


def test_support_policy_keeps_descriptive_rows_and_reports_coverage():
    meta = pd.DataFrame(
        {
            "n_total": [10, 10, 4, 10, 0],
            "n_admitted": [3, 6, 2, 9, 0],
            "representation": ["component_weight_mixture"] * 3
            + ["primary_mineral_frequency", "unknown"],
        }
    )
    original = meta.copy(deep=True)
    mask, report = support_eligibility(meta)
    assert mask.tolist() == [False, True, False, False, False]
    assert report["descriptive_intervals"] == 5 and report["excluded_intervals"] == 4
    assert report["coverage_sensitivity"] == {"0.25": 2, "0.5": 1, "0.75": 0}
    pd.testing.assert_frame_equal(meta, original)
    relaxed, _ = support_eligibility(meta, SupportPolicy(2, 0.25, True))
    assert relaxed.tolist() == [True, True, True, True, False]


def test_bad_counts_and_unknown_representation_cannot_claim_support():
    meta = pd.DataFrame(
        {
            "n_total": [10, 10, np.inf, 5, 10],
            "n_admitted": [12, np.nan, 8, 3.5, 8],
            "representation": ["component_weight_mixture"] * 4 + ["unknown"],
        }
    )
    eligible, report = support_eligibility(meta)
    assert not eligible.any()
    assert report["invalid_support_counts"] == 4
    assert report["unknown_representation_intervals"] == 1


def test_schema_learns_only_development_minerals_and_preserves_unknown_mass():
    data = matrix([[1, 0, 0], [0.7, 0.3, 0], [0.2, 0, 0.8]])
    original = data.abundance.copy()
    schema = FrozenFeatureSchema.fit(data, np.array([0, 1]), min_prevalence=0.75)
    assert schema.names == ("A", "G (rare)")
    assert "C" not in schema.mineral_to_feature and "HELD_ONLY (rare)" not in schema.names
    result = schema.transform(data)
    assert result.assignable.tolist() == [True, True, False]
    assert result.unseen_mass[2] == pytest.approx(0.8)
    assert np.square(result.x[2]).sum() == pytest.approx(0.2)
    assert np.allclose(np.square(result.x).sum(axis=1) + result.unseen_mass, 1)
    assert np.array_equal(data.abundance, original)
    restored = FrozenFeatureSchema.from_dict(json.loads(json.dumps(schema.to_dict())))
    assert restored.fingerprint == schema.fingerprint
    assert np.array_equal(restored.transform(data).x, result.x)


@pytest.mark.parametrize("values", [[[np.nan, 0, 1]], [[-0.1, 0.1, 1]], [[0.2, 0.2, 0.2]]])
def test_schema_rejects_malformed_composition(values):
    with pytest.raises(ValueError):
        FrozenFeatureSchema.fit(matrix(values))


def test_missing_weights_are_explicit_frequency_and_preserve_all_reading_ids(tmp_path):
    source = tmp_path / "dataset" / "scalar_logs.parquet"
    source.parent.mkdir()
    pd.DataFrame(
        {
            "start_depth": [0.1, 0.2, 0.3, 0.4, 0.5],
            "source_row": [11, 12, 13, 14, 15],
            "min1_u_tsas": ["A", "A", "Aspectral", "B", "A"],
            "min2_u_tsas": ["C"] * 5,
            "wt2_u_tsas": [100.0] * 5,
            "error_u_tsas": [1e9] * 5,
        }
    ).to_parquet(source, index=False)
    table, report = interval_features(source, "H", "swir")
    assert report["representation"] == "primary_mineral_frequency"
    assert report["weights_available"] is False
    assert table.iloc[0].minerals == {"A": 0.75, "B": 0.25}
    assert table.iloc[0].source_rows == [11, 12, 13, 14, 15]
    assert table.iloc[0].diagnostic_fraction == 0.8
    assert table.iloc[0].error_mean == 1e9  # descriptive; no invented accuracy cutoff
    eligible, _ = support_eligibility(table)
    assert not eligible.any()


def test_district_split_selects_k_from_development_and_reports_unassigned(monkeypatch):
    # One district has a held-only mineral. Schema/K must never learn from it
    # when that district is held. Both holes of a district stay together.
    values = [[1, 0, 0], [0.5, 0.5, 0], [1, 0, 0], [0.6, 0.4, 0], [0.1, 0, 0.9], [0, 0, 1]]
    data = matrix(values, ["H0", "H0", "H1", "H1", "H2", "H2"])
    calls = []

    def score(x, ks, seed, **kwargs):
        calls.append(x.copy())
        return pd.DataFrame({"k": [2], "silhouette": [0.4], "min_share": [0.2]})

    class Model:
        def __init__(self, x):
            self.cluster_centers_ = np.vstack([x[0], x[-1]])
            self.labels_ = np.arange(len(x)) % 2

    monkeypatch.setattr(robustness.clusters, "score_k", score)
    monkeypatch.setattr(robustness.clusters, "_kmeans", lambda x, k, seed: Model(x))
    result = robustness.district_transfer(
        data, 22, {"H0": 0, "H1": 0, "H2": 1}, repeats=4, share=0.2, seed=3
    )
    assert len(calls) == 4
    for row, training in zip(result["repeats"], calls, strict=True):
        assert row["selected_k"] == 2  # global K=22 cannot influence selection
        assert len(training) == row["development_intervals"]
        assert row["held_intervals"] + row["development_intervals"] == 6
        assert row["held_holes"] in (1, 2)
        if row["held_district_ids"] == [1]:
            assert "C" not in row["schema"]["mineral_to_feature"]
            assert row["held_unassigned_intervals"] == 2
            assert row["held_unseen_minerals"] == ["C"]
            assert row["status"] == "no_assignable_held_intervals"
    assert result["mean_ari"] is None
    assert result["transductive_reference"]["enabled"] is False


def test_unknown_districts_are_reported_and_not_assumed_a_geographic_block():
    data = matrix([[1, 0, 0], [0, 1, 0]], ["H0", "H1"])
    result = robustness.district_transfer(data, 2, {"H0": 0})
    assert result["status"] == "insufficient_districts"
    assert result["unlocated_holes_excluded"] == 1


def test_configurable_loader_count_does_not_lose_low_count_composition(monkeypatch):
    frames = iter(
        [
            pd.DataFrame(
                {
                    "feature_set_id": ["F"],
                    "bin": [1],
                    "hole_id": ["H"],
                    "depth_from_m": [1],
                    "depth_to_m": [2],
                    "n_total": [2],
                    "n_admitted": [2],
                    "error_mean": [4],
                    "mask_status": ["unavailable"],
                    "representation": ["component_weight_mixture"],
                }
            ),
            pd.DataFrame({"feature_set_id": ["F"], "bin": [1], "mineral": ["A"], "w": [1]}),
            pd.DataFrame({"mineral": ["A"], "grp": ["G"]}),
        ]
    )
    sqls = []

    def copy(conn, sql):
        sqls.append(sql)
        return next(frames)

    monkeypatch.setattr(data_module, "_copy_csv", copy)
    loaded = data_module.load_intervals(None, "swir", SupportPolicy(2, 0.5))
    assert len(loaded) == 1 and loaded.abundance[0, 0] == 1
    assert "n_admitted >= 2" in sqls[1]


def test_empty_eligible_loader_preserves_explicit_report(monkeypatch):
    frames = iter(
        [
            pd.DataFrame(
                {
                    "feature_set_id": ["F"],
                    "bin": [1],
                    "hole_id": ["H"],
                    "n_total": [10],
                    "n_admitted": [1],
                    "representation": ["component_weight_mixture"],
                }
            ),
            pd.DataFrame(columns=["feature_set_id", "bin", "mineral", "w"]),
            pd.DataFrame(columns=["mineral", "grp"]),
        ]
    )
    monkeypatch.setattr(data_module, "_copy_csv", lambda conn, sql: next(frames))
    data = data_module.load_intervals(None, "swir")
    assert len(data) == 0 and data.abundance.shape == (0, 0)
    assert data.quality_report["excluded_intervals"] == 1


def test_width_sensitivity_empty_and_constant_distances_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(robustness, "select_dataset", lambda path: tmp_path / "unused")
    table = pd.DataFrame(
        {
            "feature_set_id": ["F"],
            "bin": [0],
            "hole_id": ["H"],
            "depth_from_m": [0],
            "depth_to_m": [1],
            "n_total": [10],
            "n_admitted": [1],
            "representation": ["component_weight_mixture"],
            "minerals": [{"A": 1.0}],
        }
    )
    monkeypatch.setattr(
        robustness,
        "interval_features",
        lambda *args: (table.copy(), {"mineral_groups": {"A": "G"}}),
    )
    result = robustness.width_sensitivity(tmp_path, "swir", ["H"], widths=(1.0,))
    assert result["status"] == "no_valid_1m_reference"
    assert result["widths"]["1.0"]["status"] == "no_valid_model"
    table["n_admitted"] = 8
    monkeypatch.setattr(
        robustness, "_hole_distances", lambda *args: (["A", "B", "C"], np.zeros((3, 3)), 2)
    )
    result = robustness.width_sensitivity(tmp_path, "swir", ["H"], widths=(1.0, 2.0))
    assert result["mantel_vs_1m"]["2.0"] is None
    assert "constant" in result["comparison_notes"]["2.0"]
    json.dumps(result, allow_nan=False)


def test_final_robustness_fit_rechecks_collapsed_and_rare_groups(monkeypatch):
    class Model:
        labels_ = np.zeros(1000, dtype=int)

    monkeypatch.setattr(robustness.clusters, "_kmeans", lambda *args: Model())
    with pytest.raises(robustness.clusters.NoValidClusteringError, match="collapsed"):
        robustness._supported_fit(np.ones((1000, 2)), 2, 42)
    Model.labels_ = np.r_[np.zeros(999, dtype=int), 1]
    with pytest.raises(robustness.clusters.NoValidClusteringError, match="minimum cluster share"):
        robustness._supported_fit(np.ones((1000, 2)), 2, 42)


def test_width_fingerprints_apply_same_ten_interval_floor_as_main():
    data = matrix([[1, 0, 0]] * 19, ["A"] * 10 + ["short"] * 9)
    kept = robustness._width_supported(data)
    assert kept.meta.hole_id.unique().tolist() == ["A"] and len(kept) == 10
    report = kept.quality_report["fingerprint_support"]
    assert report["holes_excluded"] == 1 and report["intervals_excluded"] == 9
    assert report["excluded_holes"] == [{"hole_id": "short", "eligible_intervals": 9}]

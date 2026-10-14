"""Regression coverage for supported K selection and population-aware geometry."""

import json

import numpy as np
import pandas as pd
import pytest
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import cdist

from hylogger.explorer import clusters, fingerprints, rawcluster


def test_two_groups_are_considered_for_intervals_and_holes():
    rng = np.random.default_rng(31)
    x = np.vstack([rng.normal(-4, 0.08, (40, 2)), rng.normal(4, 0.08, (40, 2))])
    table = clusters.score_k(x, clusters.FINE_K, 42)
    assert clusters.choose_k(table) == 2
    hole_x = np.vstack([rng.normal(-4, 0.08, (5, 2)), rng.normal(4, 0.08, (5, 2))])
    labels, card, _ = fingerprints.cluster_holes(cdist(hole_x, hole_x))
    assert card["k"] == len(np.unique(labels)) == 2
    assert card["scores"][0]["k"] == card["scores"][0]["actual_k"] == 2


def test_failed_share_gate_and_collapsed_candidates_are_explicit_failures():
    table = pd.DataFrame({"k": [2, 3], "silhouette": [0.9, 0.8], "min_share": [0.001, 0.002]})
    with pytest.raises(clusters.NoValidClusteringError) as exc:
        clusters.choose_k(table)
    assert len(exc.value.scores) == 2
    collapsed = clusters.score_k(np.ones((12, 2)), (2, 3), 42)
    assert not collapsed.valid.any()
    assert (collapsed.actual_k == 1).all()
    with pytest.raises(clusters.NoValidClusteringError):
        clusters.choose_k(collapsed)
    with pytest.raises(clusters.NoValidClusteringError):
        clusters.choose_k(clusters.score_k(np.ones((2, 2)), (2,), 42))


def test_final_fit_rechecks_share_gate_after_candidate_subsampling(monkeypatch):
    candidate_scores = pd.DataFrame(
        {"k": [2], "silhouette": [0.8], "min_share": [0.1], "valid": [True]}
    )
    monkeypatch.setattr(clusters, "score_k", lambda *_: candidate_scores)

    class FinalModel:
        labels_ = np.r_[np.zeros(999, dtype=int), 1]

    monkeypatch.setattr(clusters, "_kmeans", lambda *_: FinalModel())
    abundance = np.tile([0.5, 0.5], (1000, 1))
    with pytest.raises(clusters.NoValidClusteringError, match="Final interval fit failed"):
        clusters.fit(np.sqrt(abundance), np.arange(1000), abundance, ["A", "B"])


def test_mixed_valid_invalid_interval_scores_and_model_card_are_strict_json():
    abundance = np.repeat([[1.0, 0.0], [0.0, 1.0]], 10, axis=0)
    table = clusters.score_k(np.sqrt(abundance), (2, 3), 42)
    records = table.to_dict("records")
    assert records[0]["valid"] and records[0]["reason"] is None
    assert not records[1]["valid"] and isinstance(records[1]["reason"], str)
    assert clusters.choose_k(table) == 2
    json.dumps(records, allow_nan=False)
    result = clusters.fit(np.sqrt(abundance), np.arange(20), abundance, ["A", "B"])
    assert result["k"] == 2
    json.dumps(result["card"], allow_nan=False)


def test_weighted_ward_changes_merge_and_matches_replicated_population():
    centres = np.array([[0.0], [1.0], [3.0]])
    weights = np.array([100, 100, 1])
    weighted = clusters.weighted_ward(centres, weights)
    unweighted = linkage(centres, method="ward")
    assert set(unweighted[0, :2]) == {0, 1}
    assert set(weighted[0, :2]) == {1, 2}
    repeated = np.repeat(centres, weights, axis=0)
    reference = linkage(repeated, method="ward")
    # All duplicate copies merge at zero cost before the three fine populations.
    assert np.allclose(weighted[:, 2], reference[-2:, 2])
    parent = clusters.tree_cut(weighted, 2)
    assert parent[1] == parent[2] != parent[0]


def test_every_weighted_ward_merge_minimises_brute_sse_increase():
    centres = np.array([[0, 1], [1, 0], [3, 2], [7, 1]], dtype=float)
    weights = np.array([20, 8, 2, 40])
    tree = clusters.weighted_ward(centres, weights)
    active = {i: [i] for i in range(4)}

    def sse(members):
        mean = np.average(centres[members], axis=0, weights=weights[members])
        return float(np.sum(weights[members, None] * (centres[members] - mean) ** 2))

    for step, (a, b, height, leaves) in enumerate(tree):
        pairs = [(i, j) for i in active for j in active if i < j]
        costs = [sse(active[i] + active[j]) - sse(active[i]) - sse(active[j]) for i, j in pairs]
        assert height**2 / 2 == pytest.approx(min(costs))
        combined = active.pop(int(a)) + active.pop(int(b))
        assert leaves == len(combined)
        active[4 + step] = combined


@pytest.mark.parametrize("off_diagonal", [0.0, 1.0])
def test_equal_distances_do_not_manufacture_supported_clusters(off_diagonal):
    distance = np.full((8, 8), off_diagonal)
    np.fill_diagonal(distance, 0)
    labels, card, _ = fingerprints.cluster_holes(distance)
    assert card["k"] == len(np.unique(labels)) == 1
    assert card["status"] == "no_distance_structure"
    assert card["scores"] == []


def test_tied_tree_cuts_report_the_actual_number_of_clusters():
    distance = np.ones((6, 6))
    distance[:3, :3] = distance[3:, 3:] = 0
    labels, card, _ = fingerprints.cluster_holes(distance)
    assert card["k"] == len(np.unique(labels)) == 2
    assert card["tied_merge_heights"]
    assert all(row["actual_k"] == row["k"] for row in card["scores"])


def test_outlier_split_is_unsupported_without_removing_its_distance_evidence():
    # A remote single hole dominates silhouette, but no candidate has repeated
    # support for every group. Retain that hole and all pair distances.
    distance = np.zeros((9, 9))
    distance[-1, :-1] = distance[:-1, -1] = 10
    original = distance.copy()
    labels, card, leaves = fingerprints.cluster_holes(distance)
    assert np.array_equal(distance, original)
    assert len(labels) == len(leaves) == 9
    assert card["k"] == 1 and card["status"] == "no_valid_candidates"
    assert card["descriptive_container"] is True
    assert card["grouping_version"] == fingerprints.HOLE_CLUSTER_VERSION
    assert card["support_policy"]["minimum_holes_per_group"] == 2
    assert all(not row["valid"] and row["min_size"] == 1 for row in card["scores"])
    assert card["scores"][0]["cluster_sizes"] == [8, 1]
    assert all(sum(row["cluster_sizes"]) == 9 for row in card["scores"])
    json.dumps(card, allow_nan=False)
    # Explicit exploratory relaxation records its policy rather than silently
    # accepting a failed default gate.
    exploratory_labels, exploratory, _ = fingerprints.cluster_holes(distance, min_holes_per_group=1)
    assert len(np.unique(exploratory_labels)) == exploratory["k"] == 2
    assert exploratory["support_policy"]["minimum_holes_per_group"] == 1
    assert exploratory["descriptive_container"] is False
    json.dumps(exploratory, allow_nan=False)


def test_replication_floor_keeps_supported_two_group_solution():
    distance = np.full((8, 8), 5.0)
    distance[:4, :4] = distance[4:, 4:] = 0
    labels, card, _ = fingerprints.cluster_holes(distance)
    assert card["status"] == "selected" and card["k"] == len(np.unique(labels)) == 2
    selected = next(row for row in card["scores"] if row["k"] == card["k"])
    assert selected["valid"] and selected["cluster_sizes"] == [4, 4]
    assert any(not row["valid"] for row in card["scores"])
    assert selected["reason"] is None
    json.dumps(card, allow_nan=False)  # Mixed valid/invalid reasons must remain JSON null.
    with pytest.raises(ValueError, match="positive integer"):
        fingerprints.cluster_holes(distance, min_holes_per_group=0)


def test_sliced_distance_detects_dependence_that_marginals_miss():
    a = np.tile([[-1, -1], [1, 1]], (10, 1))
    b = np.tile([[-1, 1], [1, -1]], (10, 1))
    scores = np.vstack([a, b, a])
    holes = pd.Series(np.repeat(["A", "B", "C"], 20))
    _, marginal_profiles = fingerprints.quantile_profiles(holes, scores)
    legacy = fingerprints.wasserstein_matrix(marginal_profiles, np.array([0.5, 0.5]))
    names, joint, card = fingerprints.sliced_wasserstein(holes, scores)
    _, again, repeated_card = fingerprints.sliced_wasserstein(holes, scores)
    assert legacy[0, 1] == 0
    assert names == ["A", "B", "C"]
    assert joint[0, 1] > 0.25 and joint[0, 2] == 0
    assert np.array_equal(joint, again) and card == repeated_card
    assert np.array_equal(joint, joint.T) and np.all(np.diag(joint) == 0)
    assert (joint >= 0).all()
    assert card["n_projections"] == 64 and card["projection_seed"] == 1729


def test_one_dimension_and_zero_variance_sliced_inputs():
    holes = pd.Series(["A"] * 10 + ["B"] * 10)
    _, distance, card = fingerprints.sliced_wasserstein(holes, np.ones((20, 1)))
    assert not distance.any() and card["n_projections"] == 1


def test_raw_support_floor_applies_after_finite_filtering():
    holes = ["A"] * 10 + ["B"] * 10 + ["short"] * 9 + ["one"]
    meta = pd.DataFrame({"hole_id": holes})
    spectra = np.ones((len(meta), 4))
    spectra[10, 0] = np.nan  # B has ten rows but only nine usable intervals.
    kept, usable, report = rawcluster.supported_intervals(meta, spectra)
    assert kept.hole_id.unique().tolist() == ["A"] and len(usable) == 10
    assert report["holes_excluded"] == 3 and report["nonfinite_intervals_excluded"] == 1
    counts = {r["hole_id"]: r["finite_intervals"] for r in report["excluded_holes"]}
    assert counts == {"B": 9, "one": 1, "short": 9}


def _raw_fixture(seed=3, channels=8):
    rng = np.random.default_rng(seed)
    holes = ["A"] * 25 + ["B"] * 25 + ["short"] * 9 + ["one"]
    patterns = np.vstack([np.sin(np.linspace(0, 4, channels)), np.cos(np.linspace(0, 4, channels))])
    spectra = 5 + patterns[np.r_[np.zeros(25, dtype=int), np.ones(35, dtype=int)]]
    spectra += rng.normal(0, 0.01, spectra.shape)
    meta = pd.DataFrame({"hole_id": holes, "albedo": 1.0})
    return meta, spectra, np.linspace(8000, 12000, channels)


def test_raw_analysis_excludes_sparse_holes_from_all_outputs_and_is_reproducible(monkeypatch):
    meta, spectra, grid = _raw_fixture()
    monkeypatch.setattr(rawcluster, "load_sensor", lambda *_: (meta.copy(), spectra, grid))
    first = rawcluster.analyse(pd.DataFrame(), "tir")
    second = rawcluster.analyse(pd.DataFrame(), "tir")
    assert first["names"] == first["shares"].index.tolist() == ["A", "B"]
    assert set(first["meta"].hole_id) == {"A", "B"}
    assert first["card"]["support"]["holes_excluded"] == 2
    assert first["card"]["intervals"] == 50
    assert first["card"]["distance_version"] == "sliced-wasserstein-v1"
    assert first["card"]["legacy_comparator"]["distance_version"].startswith("marginal-")
    assert first["legacy_wasserstein"].shape == first["wasserstein"].shape == (2, 2)
    assert np.array_equal(first["wasserstein"], second["wasserstein"])
    assert np.array_equal(first["meta"].spectral_class, second["meta"].spectral_class)


def test_raw_insufficient_support_is_explicit_and_preserves_exclusion_evidence(monkeypatch):
    meta = pd.DataFrame({"hole_id": ["A"] * 10 + ["short"] * 9})
    spectra = np.ones((19, 4))
    monkeypatch.setattr(rawcluster, "load_sensor", lambda *_: (meta, spectra, np.arange(4)))
    with pytest.raises(clusters.NoValidClusteringError) as exc:
        rawcluster.analyse(pd.DataFrame(), "tir")
    assert exc.value.details["holes_eligible"] == 1
    assert exc.value.details["excluded_holes"][0]["hole_id"] == "short"


def test_raw_pca_cap_reports_actual_retained_variance(monkeypatch):
    rng = np.random.default_rng(66)
    spectra = rng.normal(size=(120, 40))
    meta = pd.DataFrame({"hole_id": ["A"] * 60 + ["B"] * 60})
    monkeypatch.setattr(rawcluster, "load_sensor", lambda *_: (meta, spectra, np.arange(40)))
    result = rawcluster.analyse(pd.DataFrame(), "tir")
    card = result["card"]
    assert card["pca_components"] == card["pca_component_cap"] == 12
    assert card["pca_variance"] < 0.95
    assert card["pca_variance_target_met"] is False


def test_raw_grid_exclusions_are_reported(monkeypatch, tmp_path):
    paths = [tmp_path / name for name in ["A", "B", "missing"]]
    for path in paths[:2]:
        path.mkdir()
        (path / "raw_tir.npz").touch()
    products = pd.DataFrame({"hole_id": ["A", "B", "missing"], "product_path": paths})

    def load(path, sensor):
        meta = pd.DataFrame({"hole_id": [path.name] * 10})
        grid = np.array([1, 2, 3] if path.name == "A" else [1, 2, 4])
        return meta, grid, np.ones((10, 3))

    monkeypatch.setattr(rawcluster, "load_spectra", load)
    meta, _, _ = rawcluster.load_sensor(products, "tir")
    report = meta.attrs["load_report"]
    assert report["products_requested"] == 3 and report["products_loaded"] == 1
    assert {r["reason"] for r in report["excluded_products"]} == {
        "missing_spectral_summary",
        "incompatible_wavelength_grid",
    }

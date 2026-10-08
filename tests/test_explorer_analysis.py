from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon
from sklearn.metrics import adjusted_rand_score

from hylogger.explorer import anomaly, clusters, domains, fingerprints, prediction
from hylogger.explorer.data import balanced_sample


def _mixtures(seed: int = 0) -> tuple[np.ndarray, np.ndarray, list[str]]:
    rng = np.random.default_rng(seed)
    minerals = ["Muscovite", "Chlorite", "Calcite", "Quartz", "Talc"]
    centres = np.array(
        [
            [0.8, 0.1, 0.05, 0.05, 0],
            [0.05, 0.8, 0.05, 0.1, 0],
            [0.05, 0.05, 0.8, 0.1, 0],
            [0, 0.1, 0, 0.1, 0.8],
        ]
    )
    truth = rng.integers(0, len(centres), 2400)
    abundance = np.vstack([rng.dirichlet(80 * centres[t] + 0.5) for t in truth]).astype(np.float32)
    return abundance, truth, minerals


def test_choose_k_prefers_simplest_near_best():
    table = pd.DataFrame(
        {"k": [4, 6, 8], "silhouette": [0.40, 0.415, 0.42], "min_share": [0.1, 0.05, 0.001]}
    )
    assert clusters.choose_k(table) == 4  # 8 fails the min-share gate; 4 is within 0.02


def test_name_profile_dominant_and_mixed():
    assert (
        clusters.name_profile(pd.Series({"Muscovite": 0.7, "Quartz": 0.3})) == "Muscovite-dominant"
    )
    assert clusters.name_profile(pd.Series({"A": 0.4, "B": 0.35, "C": 0.25})) == "A + B + C"


def test_fit_recovers_synthetic_groups_and_assigns():
    abundance, truth, minerals = _mixtures()
    x = np.sqrt(abundance)
    holes = pd.Series(np.repeat([f"H{i}" for i in range(24)], 100))
    train = balanced_sample(holes, 60, 0)
    result = clusters.fit(x, train, abundance, minerals, seed=0)
    coarse = result["coarse_labels"]
    assert adjusted_rand_score(truth, result["labels"]) > 0.5
    assert result["card"]["k_coarse"] <= result["k"]
    labels, parent, distance = clusters.assign(x[:50], result["centres"], result["parent"])
    assert np.array_equal(labels, result["labels"][:50]) and np.array_equal(parent, coarse[:50])
    assert (distance >= 0).all()


def test_segment_finds_change_in_mean():
    rng = np.random.default_rng(1)
    x = np.vstack([rng.normal(0, 0.05, (40, 3)), rng.normal(1, 0.05, (30, 3))])
    segments = domains.segment(x, penalty=1.0)
    assert segments == [(0, 40), (40, 70)]
    labels = domains.hole_domains(np.arange(70, dtype=float), x)
    assert labels[39] != labels[40] and len(set(labels)) == 2


def test_gap_always_starts_new_domain():
    x = np.zeros((10, 2))
    depth = np.r_[np.arange(5), np.arange(20, 25)].astype(float)
    assert len(set(domains.hole_domains(depth, x))) == 2


def test_jensen_shannon_matches_scipy_and_wasserstein_is_metric_like():
    rng = np.random.default_rng(2)
    p = rng.dirichlet(np.ones(6), 7)
    d = fingerprints.jensen_shannon(p)
    assert np.allclose(d[2, 5], jensenshannon(p[2], p[5], base=2))
    assert np.allclose(d, d.T) and np.allclose(np.diag(d), 0)
    names, profiles = fingerprints.quantile_profiles(
        pd.Series(np.repeat(list("ABC"), 50)), rng.normal(size=(150, 2))
    )
    w = fingerprints.wasserstein_matrix(profiles, np.array([0.7, 0.3]))
    assert names == ["A", "B", "C"] and np.allclose(np.diag(w), 0) and np.allclose(w, w.T)


def test_anomaly_scores_flag_planted_outlier():
    abundance, _, minerals = _mixtures(3)
    abundance[100] = np.array([0, 0, 0, 0.05, 0.95], dtype=np.float32)
    x = np.sqrt(abundance)
    meta = pd.DataFrame({"hole_id": "H", "depth_from_m": np.arange(len(x), dtype=float)})
    relative = np.ones(len(x), dtype=np.float32)
    relative[100] = 10
    score, reasons, _ = anomaly.interval_scores(
        meta, x, abundance, minerals, relative, np.arange(len(x))
    )
    assert score[100] > np.quantile(score, 0.99)
    assert "poor_fit" in reasons[100]


def test_prediction_cv_beats_baseline_on_smooth_field():
    rng = np.random.default_rng(4)
    lat = rng.uniform(-31, -30, 200)
    lon = rng.uniform(121, 122, 200)
    share = (lon - 121)[:, None]
    values = np.hstack([share, 1 - share]) * 0.9 + 0.05
    holes = pd.DataFrame(
        {
            "hole_id": [f"H{i}" for i in range(200)],
            "lat": lat,
            "lon": lon,
            "district_id": (lon * 10).astype(int),
            "tectonic_region": "R",
        }
    )
    table, by_distance, summary = prediction.cross_validate(holes, values)
    assert summary["loho_skill_vs_region"] > 0.5
    assert table.loho_error.median() < table.region_error.median()


def test_assay_cleaning_separates_codes_from_detection_limits():
    from hylogger.explorer.chemistry import _clean

    cleaned = _clean(pd.Series([-0.01, -5000.0, 12.0, 2_000_000.0]))
    assert cleaned.iloc[0] == 0.005  # below detection: half the limit
    assert np.isnan(cleaned.iloc[1])  # reporting code, not a value
    assert cleaned.iloc[2] == 12.0
    assert np.isnan(cleaned.iloc[3])  # impossible concentration
    assert np.isnan(_clean(pd.Series([140.0]), percent=True).iloc[0])


def test_element_specific_plausibility_caps():
    from hylogger.explorer.chemistry import PLAUSIBLE_PPM, _clean

    cleaned = _clean(pd.Series([2780.0, 190_000.0]), cap=PLAUSIBLE_PPM["U_PPM"])
    assert cleaned.iloc[0] == 2780.0 and np.isnan(cleaned.iloc[1])


def test_virtual_assay_learns_chemistry_and_flags_mismatch():
    from hylogger.explorer import virtual_assay

    rng = np.random.default_rng(5)
    n = 3000
    mica = rng.uniform(0, 1, n)
    x = np.c_[np.sqrt(mica), np.sqrt(1 - mica)].astype(np.float32)
    meta = pd.DataFrame(
        {
            "hole_id": np.repeat([f"H{i}" for i in range(30)], 100),
            "bin": np.tile(np.arange(100), 30),
        }
    )
    k2o = 0.2 + 6 * mica
    k2o[7] = 40.0  # chemistry that the mineralogy cannot explain
    joined = pd.DataFrame(
        {"rows": [np.array([i]) for i in range(n)], "hole_id": meta.hole_id, "K2O": k2o}
    )
    result = virtual_assay.fit(meta, x, joined)
    skill = {r["analyte"]: r for r in result["skill"]}
    assert skill["K2O"]["r2_holdout_holes"] > 0.8
    mismatch = virtual_assay.mismatch_by_bin(meta, joined, result["z"])
    assert mismatch.iloc[0][["hole_id", "bin"]].tolist() == ["H0", 7]
    predicted = virtual_assay.predict(result["models"], x[:5])
    assert predicted.K2O.between(0.1, 7).all()


def test_minimum_curvature_desurvey():
    from hylogger.explorer.desurvey import Desurvey, tvd_stations

    md, tvd = tvd_stations(np.array([0, 100, 200.0]), np.array([-90, -90, -90.0]), np.zeros(3))
    assert np.allclose(tvd, md)  # vertical hole
    md, tvd = tvd_stations(np.array([50, 300.0]), np.array([-60, -60.0]), np.array([90, 90.0]))
    assert np.isclose(tvd[-1], 300 * np.sin(np.radians(60)))  # constant inclination
    survey = Desurvey({"H": (md, tvd, float(np.sin(np.radians(60))))})
    assert np.isclose(survey.tvd("H", np.array([400.0]))[0], 400 * np.sin(np.radians(60)))
    assert survey.tvd("unsurveyed", np.array([10.0]))[0] == 10.0

from __future__ import annotations

import numpy as np
import pandas as pd

from hylogger.explorer import sequence
from hylogger.explorer.desurvey import Desurvey


def _seq(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=np.float32)[:, None]


def test_identical_sequences_have_zero_distance_and_diagonal_path():
    a = _seq([0, 0, 1, 1, 2, 2])
    distance, path = sequence.dtw_path(a, a)
    assert distance == 0 and path == [(i, i) for i in range(6)]


def test_stretching_is_cheap_but_reordering_is_not():
    a = _seq([0] * 10 + [1] * 10)
    stretched = _seq([0] * 14 + [1] * 8)  # same order, different thickness
    reversed_ = _seq([1] * 10 + [0] * 10)  # same mix, opposite order
    d_stretch, _ = sequence.dtw_path(a, stretched, band=0.4)
    d_reverse, _ = sequence.dtw_path(a, reversed_, band=0.4)
    assert d_stretch < 0.05 < d_reverse


def test_batch_matches_pairwise_and_matrix_is_symmetric():
    rng = np.random.default_rng(0)
    seqs = [rng.normal(size=(n, 3)).astype(np.float32) for n in (5, 9, 12, 7)]
    padded, lengths = sequence._pad(seqs)
    batch = sequence.dtw_against(seqs[1], padded, lengths)
    for j, other in enumerate(seqs):
        pairwise, _ = sequence.dtw_path(seqs[1], other)
        assert np.isclose(batch[j], pairwise, atol=1e-4)
    matrix = sequence.dtw_matrix(seqs)
    assert np.allclose(matrix, matrix.T) and np.allclose(np.diag(matrix), 0)


def test_steps_follow_true_vertical_depth_and_zones_group_clusters():
    meta = pd.DataFrame(
        {
            "hole_id": ["A"] * 40,
            "depth_from_m": np.arange(40.0),
            "depth_to_m": np.arange(1.0, 41.0),
        }
    )
    z = np.r_[np.zeros((20, 2)), np.ones((20, 2))].astype(np.float32)
    labels = np.r_[np.zeros(20, int), np.ones(20, int)]
    inclined = Desurvey({"A": (np.array([0.0, 40.0]), np.array([0.0, 20.0]), 0.5)})
    steps = sequence.build_steps(meta, z, labels, inclined, step_m=5)
    assert steps.tvd_from_m.max() < 20  # 40 m along the hole is 20 m vertical
    zones = sequence.matched_zones([(i, i) for i in range(len(steps))], steps, steps)
    assert [z["cluster_id"] for z in zones] == [0, 1]
    assert all(z["same_cluster_share"] == 1 for z in zones)

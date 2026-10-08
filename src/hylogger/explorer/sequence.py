"""Depth-sequence similarity between holes with dynamic time warping (DTW).

The whole-hole fingerprints compare the overall mineral mix and ignore order.
Here each hole becomes a sequence of fixed true-vertical-depth steps (default
10 m): each step is the mean of its 1 m intervals in a compact PCA space of the
Hellinger mineral features. DTW aligns two sequences monotonically, letting a
zone stretch or compress (a 20 m chlorite zone can match a 50 m one), like
correlating two stratigraphic columns. A Sakoe-Chiba band on relative position
(BAND) stops pathological warps, and the accumulated cost is divided by the
summed sequence lengths so long and short holes are comparable.

Distances for all pairs are computed with an anti-diagonal wavefront that is
vectorised across every other hole at once.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

STEP_M = 10.0
COMPONENTS = 8
BAND = 0.3
MIN_STEPS = 3
MAX_STEPS = 120  # very deep holes use a coarser step so memory stays bounded


def reduce(x: np.ndarray, train: np.ndarray, seed: int = 42) -> np.ndarray:
    pca = PCA(n_components=min(COMPONENTS, x.shape[1]), random_state=seed).fit(x[train])
    return pca.transform(x).astype(np.float32)


def build_steps(
    meta: pd.DataFrame, z: np.ndarray, labels: np.ndarray, survey, step_m: float = STEP_M
) -> pd.DataFrame:
    """One row per (hole, step): TVD and measured-depth range, mean features, modal cluster."""
    rows = []
    for hole_id, index in meta.groupby("hole_id").indices.items():
        start = meta.depth_from_m.to_numpy()[index]
        end = meta.depth_to_m.to_numpy()[index]
        tvd = survey.tvd(hole_id, (start + end) / 2)
        span = float(tvd.max() - tvd.min()) if len(tvd) else 0.0
        hole_step = max(step_m, np.ceil(span / MAX_STEPS / step_m) * step_m)
        step = np.floor(tvd / hole_step).astype(int)
        for value in np.unique(step):
            members = index[step == value]
            mask = step == value
            rows.append(
                {
                    "hole_id": hole_id,
                    "step": int(value),
                    "tvd_from_m": float(value * hole_step),
                    "step_m": float(hole_step),
                    "md_from_m": float(start[mask].min()),
                    "md_to_m": float(end[mask].max()),
                    "n": int(len(members)),
                    "cluster_id": int(np.bincount(labels[members]).argmax()),
                    **{f"c{i}": float(v) for i, v in enumerate(z[members].mean(axis=0))},
                }
            )
    return pd.DataFrame(rows)


def sequences(steps: pd.DataFrame) -> tuple[list[str], list[np.ndarray]]:
    columns = [c for c in steps.columns if c.startswith("c") and c[1:].isdigit()]
    names, seqs = [], []
    for hole_id, part in steps.sort_values(["hole_id", "step"]).groupby("hole_id"):
        if len(part) >= MIN_STEPS:
            names.append(hole_id)
            seqs.append(part[columns].to_numpy(np.float32))
    return names, seqs


def _pad(seqs: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    lengths = np.array([len(s) for s in seqs])
    out = np.zeros((len(seqs), lengths.max(), seqs[0].shape[1]), dtype=np.float32)
    for i, s in enumerate(seqs):
        out[i, : len(s)] = s
    return out, lengths


def dtw_against(
    a: np.ndarray, padded: np.ndarray, lengths: np.ndarray, band: float = BAND
) -> np.ndarray:
    """Normalised banded DTW distance from sequence `a` to every padded sequence."""
    n, lmax, d = padded.shape
    la = len(a)
    # Pairwise Euclidean costs (n, la, lmax) via one matrix product.
    flat = padded.reshape(n * lmax, d)
    sq = (flat**2).sum(axis=1)[:, None] + (a**2).sum(axis=1)[None, :] - 2 * flat @ a.T
    cost = np.sqrt(np.clip(sq, 0, None)).reshape(n, lmax, la).transpose(0, 2, 1)
    i_pos = (np.arange(la) / max(la - 1, 1))[None, :, None]
    j_pos = np.arange(lmax)[None, None, :] / np.maximum(lengths - 1, 1)[:, None, None]
    outside = (np.abs(i_pos - j_pos) > band) | (
        np.arange(lmax)[None, None, :] >= lengths[:, None, None]
    )
    cost = np.where(outside, np.inf, cost)
    acc = np.full((n, la + 1, lmax + 1), np.inf, dtype=np.float32)
    acc[:, 0, 0] = 0.0
    for k in range(2, la + lmax + 1):
        i = np.arange(max(1, k - lmax), min(la, k - 1) + 1)
        j = k - i
        best = np.minimum(np.minimum(acc[:, i - 1, j], acc[:, i, j - 1]), acc[:, i - 1, j - 1])
        acc[:, i, j] = cost[:, i - 1, j - 1] + best
    final = acc[np.arange(n), la, lengths]
    return final / (la + lengths)


def dtw_matrix(seqs: list[np.ndarray], band: float = BAND) -> np.ndarray:
    """Symmetric pairwise distances; each row only computes the upper triangle."""
    padded, lengths = _pad(seqs)
    n = len(seqs)
    out = np.full((n, n), np.nan)
    for row, a in enumerate(seqs):
        out[row, row:] = dtw_against(a, padded[row:], lengths[row:], band)
    out = np.where(np.isfinite(out), out, np.nan)
    upper = np.triu(np.nan_to_num(out, nan=-1), 1)
    out = upper + upper.T
    fill = np.nanmax(np.where(out >= 0, out, np.nan)) if (out >= 0).any() else 1.0
    out = np.where(out < 0, fill, out)  # infeasible under the band: as far as the farthest pair
    np.fill_diagonal(out, 0.0)
    return out


def dtw_path(
    a: np.ndarray, b: np.ndarray, band: float = BAND
) -> tuple[float, list[tuple[int, int]]]:
    """Distance and optimal alignment path (pairs of step indices) between two sequences."""
    la, lb = len(a), len(b)
    cost = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2))
    i_pos = np.arange(la)[:, None] / max(la - 1, 1)
    j_pos = np.arange(lb)[None, :] / max(lb - 1, 1)
    cost = np.where(np.abs(i_pos - j_pos) > band, np.inf, cost)
    acc = np.full((la + 1, lb + 1), np.inf)
    acc[0, 0] = 0.0
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            acc[i, j] = cost[i - 1, j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    if not np.isfinite(acc[la, lb]):
        return float("nan"), []
    path, i, j = [], la, lb
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        moves = [
            (acc[i - 1, j - 1], i - 1, j - 1),
            (acc[i - 1, j], i - 1, j),
            (acc[i, j - 1], i, j - 1),
        ]
        _, i, j = min(moves, key=lambda m: m[0])
    return float(acc[la, lb] / (la + lb)), path[::-1]


def matched_zones(
    path: list[tuple[int, int]], a_steps: pd.DataFrame, b_steps: pd.DataFrame
) -> list[dict]:
    """Group the path into runs where hole A keeps the same cluster: zone A <-> zone B."""
    zones: list[dict] = []
    for i, j in path:
        cluster = int(a_steps.cluster_id.iat[i])
        if zones and zones[-1]["cluster_id"] == cluster:
            zone = zones[-1]
            zone["a_to"] = float(a_steps.md_to_m.iat[i])
            zone["b_from"] = min(zone["b_from"], float(b_steps.md_from_m.iat[j]))
            zone["b_to"] = max(zone["b_to"], float(b_steps.md_to_m.iat[j]))
            zone["b_clusters"].append(int(b_steps.cluster_id.iat[j]))
        else:
            zones.append(
                {
                    "cluster_id": cluster,
                    "a_from": float(a_steps.md_from_m.iat[i]),
                    "a_to": float(a_steps.md_to_m.iat[i]),
                    "b_from": float(b_steps.md_from_m.iat[j]),
                    "b_to": float(b_steps.md_to_m.iat[j]),
                    "b_clusters": [int(b_steps.cluster_id.iat[j])],
                }
            )
    for zone in zones:
        clusters = zone.pop("b_clusters")
        zone["same_cluster_share"] = round(clusters.count(zone["cluster_id"]) / len(clusters), 3)
    return zones

"""Downhole domains: contiguous depth zones with a consistent mineral mix.

Each hole's 1 m Hellinger features are segmented by penalised optimal
partitioning (change in mean, squared-error cost, exact dynamic programme with
prefix sums). The penalty scales with the hole's own reading-to-reading noise,
so noisy holes are not over-split. Segments shorter than MIN_LENGTH bins are
not allowed, and gaps over MAX_GAP_M metres always start a new domain.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_LENGTH = 3
MAX_GAP_M = 5.0
PENALTY_FACTOR = 12.0


def segment(x: np.ndarray, penalty: float, min_length: int = MIN_LENGTH) -> list[tuple[int, int]]:
    """Optimal [start, end) segments minimising squared error + penalty per segment."""
    n = len(x)
    if n <= min_length:
        return [(0, n)]
    s1 = np.vstack([np.zeros(x.shape[1]), np.cumsum(x, axis=0)])
    s2 = np.concatenate([[0.0], np.cumsum((x**2).sum(axis=1))])
    best = np.full(n + 1, np.inf)
    best[0] = -penalty
    back = np.zeros(n + 1, dtype=int)
    for end in range(min_length, n + 1):
        starts = np.arange(0, end - min_length + 1)
        starts = starts[np.isfinite(best[starts])]
        if not len(starts):
            continue
        lengths = end - starts
        seg1 = s1[end] - s1[starts]
        cost = (s2[end] - s2[starts]) - (seg1**2).sum(axis=1) / lengths
        total = best[starts] + cost + penalty
        pick = int(np.argmin(total))
        best[end], back[end] = total[pick], starts[pick]
    if not np.isfinite(best[n]):
        return [(0, n)]
    cuts, end = [], n
    while end > 0:
        cuts.append((back[end], end))
        end = back[end]
    return cuts[::-1]


def hole_domains(depth_from: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Domain index per row of one hole (rows sorted by depth)."""
    labels = np.zeros(len(x), dtype=int)
    if len(x) == 0:
        return labels
    gaps = np.flatnonzero(np.diff(depth_from) > MAX_GAP_M) + 1
    pieces = np.split(np.arange(len(x)), gaps)
    diffs = np.diff(x, axis=0)
    noise = float((diffs**2).sum(axis=1).mean() / 2) if len(diffs) else 0.0
    penalty = PENALTY_FACTOR * max(noise, 1e-4)
    next_id = 0
    for piece in pieces:
        for start, end in segment(x[piece], penalty):
            labels[piece[start:end]] = next_id
            next_id += 1
    return labels


def all_domains(
    meta: pd.DataFrame, x: np.ndarray, abundance: np.ndarray, minerals: list[str], fine: np.ndarray
) -> tuple[np.ndarray, pd.DataFrame]:
    """Domain id per interval and one summary row per domain."""
    domain = np.zeros(len(meta), dtype=int)
    rows = []
    for hole_id, index in meta.groupby("hole_id").indices.items():
        index = index[np.argsort(meta.depth_from_m.to_numpy()[index])]
        labels = hole_domains(meta.depth_from_m.to_numpy()[index], x[index])
        domain[index] = labels
        for domain_id in np.unique(labels):
            members = index[labels == domain_id]
            mix = abundance[members].mean(axis=0)
            top = np.argsort(-mix)[:6]
            rows.append(
                {
                    "hole_id": hole_id,
                    "domain_id": int(domain_id),
                    "depth_from_m": float(meta.depth_from_m.iloc[members].min()),
                    "depth_to_m": float(meta.depth_to_m.iloc[members].max()),
                    "cluster_id": int(np.bincount(fine[members]).argmax()),
                    "minerals": {
                        minerals[i]: round(float(mix[i]), 4) for i in top if mix[i] > 0.01
                    },
                }
            )
    return domain, pd.DataFrame(rows)

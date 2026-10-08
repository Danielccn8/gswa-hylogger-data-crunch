"""Whole-hole fingerprints, distances, hole clustering and 2D embeddings.

A hole's fingerprint is the share of its intervals in each fine cluster
(Sathi, create_hole_fingerprints) plus its mean mineral abundance. Distances:
- "clusters": base-2 Jensen-Shannon distance between cluster shares
  (Sathi, compare_hole_fingerprints); 0 = identical, 1 = disjoint;
- "minerals": Hellinger distance between mean mineral abundances;
- "distribution": per-component Wasserstein distance between the holes'
  interval score distributions (Yeshwanth's raw_data_PCA idea), approximated
  with 50 quantiles and weighted by explained variance.
Holes are grouped with average linkage on the chosen distance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, leaves_list, linkage
from scipy.spatial.distance import squareform
from sklearn.manifold import TSNE
from sklearn.metrics import silhouette_score

QUANTILES = np.linspace(0.01, 0.99, 50)
HOLE_K = tuple(range(3, 16))
MIN_INTERVALS = 10


def shares(holes: pd.Series, labels: np.ndarray, k: int) -> pd.DataFrame:
    table = pd.crosstab(holes.to_numpy(), labels).reindex(columns=range(k), fill_value=0)
    return table.div(table.sum(axis=1), axis=0)


def mean_abundance(holes: pd.Series, abundance: np.ndarray, minerals: list[str]) -> pd.DataFrame:
    return pd.DataFrame(abundance, columns=minerals).groupby(holes.to_numpy()).mean()


def _blocks(p: np.ndarray, q: np.ndarray, pair, block: int = 128) -> np.ndarray:
    out = np.empty((len(p), len(q)))
    for start in range(0, len(p), block):
        out[start : start + block] = pair(p[start : start + block, None, :], q[None, :, :])
    return out


def _js_pair(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    m = 0.5 * (p + q)
    with np.errstate(divide="ignore", invalid="ignore"):
        kl_p = np.where(p > 0, p * np.log2(p / m), 0).sum(axis=2)
        kl_q = np.where(q > 0, q * np.log2(q / m), 0).sum(axis=2)
    return np.sqrt(np.clip(0.5 * (kl_p + kl_q), 0, 1))


def jensen_shannon(p: np.ndarray, q: np.ndarray | None = None) -> np.ndarray:
    """Pairwise base-2 JS distance between rows of p (and q)."""
    return _blocks(np.asarray(p, float), np.asarray(p if q is None else q, float), _js_pair)


def hellinger(p: np.ndarray, q: np.ndarray | None = None) -> np.ndarray:
    a = np.sqrt(np.asarray(p, float))
    b = np.sqrt(np.asarray(p if q is None else q, float))
    return _blocks(a, b, lambda x, y: np.sqrt(np.clip(((x - y) ** 2).sum(axis=2) / 2, 0, 1)))


def quantile_profiles(holes: pd.Series, scores: np.ndarray) -> tuple[list[str], np.ndarray]:
    names, profiles = [], []
    for hole_id, index in holes.groupby(holes).indices.items():
        names.append(hole_id)
        profiles.append(np.quantile(scores[index], QUANTILES, axis=0).T)  # (components, q)
    return names, np.asarray(profiles)


def wasserstein_matrix(profiles: np.ndarray, weights: np.ndarray) -> np.ndarray:
    n = len(profiles)
    out = np.zeros((n, n))
    for i in range(n):
        out[i] = (np.abs(profiles - profiles[i]).mean(axis=2) * weights).sum(axis=1)
    return out


def cluster_holes(distance: np.ndarray, seed: int = 42) -> tuple[np.ndarray, dict, np.ndarray]:
    n = len(distance)
    if n < 4:
        return np.zeros(n, dtype=int), {"k": 1, "scores": []}, np.arange(n)
    tree = linkage(squareform(distance, checks=False), method="average")
    scores = []
    for k in HOLE_K:
        if k >= n:
            break
        labels = fcluster(tree, k, criterion="maxclust") - 1
        if len(set(labels)) < 2:
            continue
        sizes = np.bincount(labels)
        scores.append(
            {
                "k": k,
                "silhouette": float(silhouette_score(distance, labels, metric="precomputed")),
                "singletons": int((sizes == 1).sum()),
            }
        )
    table = pd.DataFrame(scores)
    if table.empty:
        return np.zeros(n, dtype=int), {"k": 1, "scores": []}, leaves_list(tree)
    eligible = table[table.singletons <= max(1, n // 50)]
    eligible = eligible if len(eligible) else table
    best = eligible.silhouette.max()
    k = int(eligible[eligible.silhouette >= best - 0.02].k.min())
    labels = fcluster(tree, k, criterion="maxclust") - 1
    order = np.argsort(-np.bincount(labels))
    remap = np.empty(len(order), dtype=int)
    remap[order] = np.arange(len(order))
    return remap[labels], {"k": k, "scores": table.to_dict("records")}, leaves_list(tree)


def embed(distance: np.ndarray, seed: int = 42) -> np.ndarray:
    n = len(distance)
    if n < 5:
        return np.c_[np.arange(n, dtype=float), np.zeros(n)]
    perplexity = float(min(30, max(2, (n - 1) / 3)))
    model = TSNE(
        n_components=2,
        metric="precomputed",
        init="random",
        perplexity=perplexity,
        random_state=seed,
    )
    return model.fit_transform(np.asarray(distance, dtype=np.float64))


def neighbours(names: list[str], distance: np.ndarray, top: int = 50) -> pd.DataFrame:
    rows = []
    for i, hole_id in enumerate(names):
        order = [j for j in np.argsort(distance[i]) if j != i][:top]
        rows.extend(
            {
                "hole_id": hole_id,
                "rank": rank + 1,
                "neighbor_id": names[j],
                "distance": float(distance[i, j]),
            }
            for rank, j in enumerate(order)
        )
    return pd.DataFrame(rows)


def hole_anomaly(distance: np.ndarray, k: int = 5) -> np.ndarray:
    """Mean distance to the k nearest other holes, as a percentile."""
    n = len(distance)
    if n <= 1:
        return np.zeros(n)
    sorted_d = np.sort(distance + np.eye(n) * 1e9, axis=1)[:, : min(k, n - 1)]
    mean = sorted_d.mean(axis=1)
    return (pd.Series(mean).rank(pct=True)).to_numpy()

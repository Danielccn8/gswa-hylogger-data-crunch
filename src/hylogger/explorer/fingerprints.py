"""Whole-hole fingerprints, distances, hole clustering and 2D embeddings.

A hole's fingerprint is the share of its intervals in each fine cluster
(Sathi, create_hole_fingerprints) plus its mean mineral abundance. Distances:
- "clusters": base-2 Jensen-Shannon distance between cluster shares
  (Sathi, compare_hole_fingerprints); 0 = identical, 1 = disjoint;
- "minerals": Hellinger distance between mean mineral abundances;
- joint distribution: sliced Wasserstein on deterministic unit projections,
  with 50 midpoint quantiles, preserving sensitivity to component dependence;
- the per-component quantile Wasserstein utility remains a named legacy
  marginal comparator (Yeshwanth's raw_data_PCA idea).
Holes are grouped with average linkage on the chosen distance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import cut_tree, leaves_list, linkage
from scipy.spatial.distance import squareform
from sklearn.manifold import TSNE
from sklearn.metrics import silhouette_score

QUANTILES = np.linspace(0.01, 0.99, 50)
HOLE_K = tuple(range(2, 16))
MIN_INTERVALS = 10
DISTANCE_VERSION = "sliced-wasserstein-v1"
PROJECTION_SEED = 1729
N_PROJECTIONS = 64
HOLE_CLUSTER_VERSION = "average-linkage-replicated-groups-v2"


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
    """Legacy marginal-quantile W1 comparator; cannot capture joint dependence."""
    n = len(profiles)
    out = np.zeros((n, n))
    for i in range(n):
        out[i] = (np.abs(profiles - profiles[i]).mean(axis=2) * weights).sum(axis=1)
    return out


def sliced_wasserstein(
    holes: pd.Series,
    scores: np.ndarray,
    seed: int = PROJECTION_SEED,
    n_directions: int = N_PROJECTIONS,
) -> tuple[list[str], np.ndarray, dict]:
    """Deterministic finite-projection approximation to joint sliced W1.

    Coordinate axes retain marginal sensitivity; remaining uniformly sampled
    unit directions detect dependence across components. Unwhitened PCA already
    retains variance in its score scale, so no explained-variance weighting is
    applied again. This finite projection/quantile approximation is not a proof
    that distinct joint distributions will always be separated.
    """
    scores = np.asarray(scores, dtype=float)
    if scores.ndim != 2 or scores.shape[1] < 1 or len(scores) != len(holes):
        raise ValueError("Sliced Wasserstein needs aligned rows and at least one feature")
    if not np.isfinite(scores).all():
        raise ValueError("Sliced Wasserstein requires finite scores")
    dimensions = scores.shape[1]
    if n_directions < dimensions:
        raise ValueError("Projection count must include every coordinate axis")
    if dimensions == 1:
        directions = np.ones((1, 1))
    else:
        random = np.random.default_rng(seed).normal(size=(n_directions - dimensions, dimensions))
        random /= np.linalg.norm(random, axis=1, keepdims=True)
        directions = np.vstack([np.eye(dimensions), random])
    # Project one hole at a time: no intervals-by-directions array for the full cohort.
    names, profiles = [], []
    quantiles = (np.arange(50) + 0.5) / 50
    for name, indices in holes.groupby(holes).indices.items():
        names.append(name)
        projection = scores[indices] @ directions.T
        profiles.append(np.quantile(projection, quantiles, axis=0).T)
    profiles = np.asarray(profiles, dtype=np.float32)
    matrix = wasserstein_matrix(profiles, np.full(len(directions), 1 / len(directions)))
    matrix = np.maximum((matrix + matrix.T) / 2, 0)
    np.fill_diagonal(matrix, 0)
    metadata = {
        "distance_version": DISTANCE_VERSION,
        "distance": "joint sliced Wasserstein W1 (finite projections, 50 midpoint quantiles)",
        "n_projections": len(directions),
        "projection_seed": seed,
        "coordinate_axes": dimensions,
        "projection_weighting": "equal; unwhitened scores retain variance without reweighting",
        "quantile_count": 50,
        "approximation_note": (
            "finite projections and quantiles; identical distance is not proof of "
            "identical joint distributions"
        ),
    }
    return names, matrix, metadata


def cluster_holes(
    distance: np.ndarray, seed: int = 42, min_holes_per_group: int = 2
) -> tuple[np.ndarray, dict, np.ndarray]:
    """Select descriptive groups with explicitly required replicated support.

    A singleton remains useful as an unusual-hole candidate, but cannot support
    an automatically accepted group under the default replication floor.
    Unaccepted partitions return one display container, not an identified group.
    """
    if (
        not isinstance(min_holes_per_group, int)
        or isinstance(min_holes_per_group, bool)
        or min_holes_per_group < 1
    ):
        raise ValueError("Minimum holes per group must be a positive integer")
    base_card = {
        "grouping_version": HOLE_CLUSTER_VERSION,
        "candidate_k": list(HOLE_K),
        "algorithm": "average linkage; exact merge-count cut",
        "support_policy": {
            "minimum_holes_per_group": min_holes_per_group,
            "meaning": "replication floor for descriptive groups; not geological validation",
        },
        "k_rule": "smallest K within 0.02 of best silhouette among supported candidates",
    }
    distance = np.asarray(distance, dtype=float)
    n = len(distance)
    if distance.shape != (n, n) or not np.isfinite(distance).all():
        raise ValueError("Hole clustering requires a finite square distance matrix")
    if not np.allclose(distance, distance.T) or not np.allclose(np.diag(distance), 0):
        raise ValueError("Hole distances must be symmetric with a zero diagonal")
    if (distance < -1e-10).any():
        raise ValueError("Hole distances must be nonnegative")
    distance = np.maximum((distance + distance.T) / 2, 0)
    np.fill_diagonal(distance, 0)
    if n < 3:
        return (
            np.zeros(n, dtype=int),
            {
                **base_card,
                "k": min(1, n),
                "scores": [],
                "status": "insufficient_holes",
                "descriptive_container": True,
                "reason": "silhouette requires at least three holes",
            },
            np.arange(n),
        )
    condensed = squareform(distance)
    if np.allclose(condensed, condensed[0], rtol=1e-7, atol=1e-12):
        return (
            np.zeros(n, dtype=int),
            {
                **base_card,
                "k": 1,
                "scores": [],
                "status": "no_distance_structure",
                "descriptive_container": True,
                "reason": "all pairwise distances are equal; no supported partition",
            },
            np.arange(n),
        )
    tree = linkage(condensed, method="average")
    scores = []
    for k in HOLE_K:
        if k >= n:
            break
        labels = cut_tree(tree, n_clusters=[k]).reshape(-1)
        actual_k = len(np.unique(labels))
        if not 2 <= actual_k < n:
            continue
        sizes = np.bincount(labels)
        score = float(silhouette_score(distance, labels, metric="precomputed"))
        valid = sizes.min() >= min_holes_per_group and np.isfinite(score)
        scores.append(
            {
                "k": k,
                "actual_k": actual_k,
                "silhouette": score,
                "singletons": int((sizes == 1).sum()),
                "cluster_sizes": sorted(sizes.tolist(), reverse=True),
                "min_size": int(sizes.min()),
                "valid": bool(valid),
                "reason": None if valid else "minimum replicated group support not satisfied",
            }
        )
    table = pd.DataFrame(scores)
    if table.empty:
        return (
            np.zeros(n, dtype=int),
            {
                **base_card,
                "k": 1,
                "scores": [],
                "status": "no_valid_candidates",
                "descriptive_container": True,
            },
            leaves_list(tree),
        )
    eligible = table[table.valid]
    if eligible.empty:
        return (
            np.zeros(n, dtype=int),
            {
                **base_card,
                "k": 1,
                "scores": scores,
                "status": "no_valid_candidates",
                "reason": "every candidate fails the minimum replicated group support floor",
                "descriptive_container": True,
            },
            leaves_list(tree),
        )
    best = eligible.silhouette.max()
    k = int(eligible[eligible.silhouette >= best - 0.02].k.min())
    labels = cut_tree(tree, n_clusters=[k]).reshape(-1)
    order = np.argsort(-np.bincount(labels))
    remap = np.empty(len(order), dtype=int)
    remap[order] = np.arange(len(order))
    return (
        remap[labels],
        {
            **base_card,
            "k": len(order),
            "scores": scores,
            "status": "selected",
            "descriptive_container": False,
            "tied_merge_heights": bool(len(np.unique(tree[:, 2])) < len(tree)),
            "partition_note": (
                "descriptive partition selected by silhouette; not geological validation"
            ),
        },
        leaves_list(tree),
    )


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

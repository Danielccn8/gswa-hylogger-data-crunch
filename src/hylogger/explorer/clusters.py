"""Interval clustering at two granularities, with Sathi's K rule and naming.

Geometry: Hellinger (square root of mineral abundance), so distance follows
compositional differences. Fitting uses a hole-balanced sample. The fine level
is K-Means (10 starts); the coarse level merges fine centroids with Ward linkage,
so every fine cluster has one coarse parent (a two-level tree the UI can show).

K rule (scripts/tsas_data_clustering.py, choose_cluster_count): discard K with
a cluster under the minimum share, then take the smallest K whose silhouette is
within 0.02 of the best. Diagnostics follow Avantika's TIR notebook: seed
stability (pairwise ARI) and scaled-versus-unscaled agreement.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, silhouette_score
from sklearn.preprocessing import StandardScaler

FINE_K = (6, 8, 10, 12, 15, 18, 22)
COARSE_K = (3, 4, 5, 6, 7)
SILHOUETTE_TOLERANCE = 0.02
MIN_SHARE = 0.005
SEEDS = (42, 101, 202)


def _kmeans(x: np.ndarray, k: int, seed: int, n_init: int = 10) -> KMeans:
    return KMeans(n_clusters=k, random_state=seed, n_init=n_init, max_iter=300).fit(x)


def _subsample(n: int, size: int, seed: int) -> np.ndarray:
    return np.sort(np.random.default_rng(seed).choice(n, min(size, n), replace=False))


def choose_k(scores: pd.DataFrame) -> int:
    eligible = scores[scores.min_share >= MIN_SHARE]
    if eligible.empty:
        eligible = scores
    best = eligible.silhouette.max()
    return int(eligible[eligible.silhouette >= best - SILHOUETTE_TOLERANCE].k.min())


def score_k(
    x: np.ndarray, ks: tuple[int, ...], seed: int, sample: int = 15000, fit_rows: int = 60000
) -> pd.DataFrame:
    """Silhouette and smallest cluster share per K, fitted on a subsample for speed."""
    x = x[_subsample(len(x), fit_rows, seed + 7)]
    probe = _subsample(len(x), sample, seed)
    rows = []
    for k in ks:
        if k >= len(x):
            continue
        model = _kmeans(x, k, seed, n_init=3)
        labels = model.predict(x[probe])
        shares = np.bincount(model.labels_, minlength=k) / len(x)
        score = silhouette_score(x[probe], labels) if len(set(labels)) > 1 else -1
        rows.append(
            {
                "k": k,
                "silhouette": float(score),
                "min_share": float(shares.min()),
                "inertia": float(model.inertia_),
            }
        )
    return pd.DataFrame(rows)


def name_profile(profile: pd.Series, dominant_share: float = 0.5, parts: int = 3) -> str:
    """'X-dominant' when one mineral is at least half of the mix, else 'A + B + C'."""
    ordered = profile.sort_values(ascending=False)
    ordered = ordered[ordered > 0.02]
    if ordered.empty:
        return "Unclassified"
    if ordered.iloc[0] >= dominant_share:
        return f"{ordered.index[0]}-dominant"
    return " + ".join(ordered.index[:parts])


def unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return out


def fit(
    x: np.ndarray, train: np.ndarray, abundance: np.ndarray, minerals: list[str], seed: int = 42
) -> dict:
    """Fit fine and coarse clusters on rows `train`; assign every row."""
    xt = x[train]
    fine_scores = score_k(xt, FINE_K, seed)
    k = choose_k(fine_scores)
    model = _kmeans(xt, k, seed)
    # Renumber largest-first so IDs are stable and readable.
    order = np.argsort(-np.bincount(model.labels_, minlength=k))
    remap = np.empty(k, dtype=int)
    remap[order] = np.arange(k)
    centres = model.cluster_centers_[order]
    labels, _, distance = assign(x, centres, np.arange(k))
    sizes = np.bincount(labels, minlength=k)

    # Coarse level: Ward merge of the fine centroids.
    tree = linkage(centres, method="ward")
    coarse_rows = []
    probe = np.random.default_rng(seed).choice(len(xt), min(15000, len(xt)), replace=False)
    fine_probe = labels[train][probe]
    for kc in COARSE_K:
        if kc >= k:
            continue
        parent = fcluster(tree, kc, criterion="maxclust") - 1
        lab = parent[fine_probe]
        score = silhouette_score(xt[probe], lab) if len(set(lab)) > 1 else -1
        share = np.bincount(parent[labels], minlength=kc) / len(labels)
        coarse_rows.append({"k": kc, "silhouette": float(score), "min_share": float(share.min())})
    coarse_scores = pd.DataFrame(coarse_rows)
    kc = choose_k(coarse_scores) if len(coarse_scores) else k
    parent = fcluster(tree, kc, criterion="maxclust") - 1 if kc < k else np.arange(k)
    # Renumber coarse groups largest-first too.
    coarse_sizes = np.bincount(parent[labels], minlength=kc)
    corder = np.argsort(-coarse_sizes)
    cremap = np.empty(kc, dtype=int)
    cremap[corder] = np.arange(kc)
    parent = cremap[parent]

    # Diagnostics.
    stability_rows = _subsample(len(xt), 60000, seed + 3)
    seed_labels = [_kmeans(xt[stability_rows], k, s).predict(xt[probe]) for s in SEEDS]
    seed_ari = [adjusted_rand_score(seed_labels[0], other) for other in seed_labels[1:]]
    scaled = StandardScaler().fit_transform(xt[probe])
    scaled_labels = _kmeans(scaled, k, seed, n_init=3).labels_
    dominant = abundance[train][probe].argmax(axis=1)
    relative = distance / np.maximum(
        pd.Series(distance).groupby(labels).transform("median").to_numpy(), 1e-6
    )

    profiles = []
    for level, ids, count in (("fine", labels, k), ("coarse", parent[labels], kc)):
        frame = pd.DataFrame(abundance, columns=minerals)
        means = frame.groupby(ids).mean()
        names = unique_names(
            [name_profile(means.loc[c]) if c in means.index else "Empty" for c in range(count)]
        )
        for cluster in range(count):
            mix = means.loc[cluster] if cluster in means.index else pd.Series(dtype=float)
            top = mix.sort_values(ascending=False)
            profiles.append(
                {
                    "level": level,
                    "cluster_id": cluster,
                    "parent_id": int(parent[cluster]) if level == "fine" else None,
                    "name": names[cluster],
                    "n_intervals": int((ids == cluster).sum()),
                    "minerals": {m: round(float(v), 4) for m, v in top[top > 0.005].items()},
                }
            )
    card = {
        "geometry": "Hellinger (square root of mineral abundance)",
        "algorithm": "K-Means, 10 starts (fine) + Ward merge of centroids (coarse)",
        "k_fine": k,
        "k_coarse": int(kc),
        "fine_scores": fine_scores.to_dict("records"),
        "coarse_scores": coarse_scores.to_dict("records"),
        "k_rule": (
            f"smallest K within {SILHOUETTE_TOLERANCE} of best silhouette; min share {MIN_SHARE}"
        ),
        "seed_ari": [round(float(v), 4) for v in seed_ari],
        "scaled_vs_hellinger_ari": round(float(adjusted_rand_score(fine_probe, scaled_labels)), 4),
        "dominant_mineral_baseline": {
            "ari": round(float(adjusted_rand_score(dominant, fine_probe)), 4),
            "nmi": round(float(normalized_mutual_info_score(dominant, fine_probe)), 4),
            "note": "Agreement with grouping each interval by its largest mineral (Sathi baseline)",
        },
        "training_rows": int(len(train)),
        "rows": int(len(x)),
        "cluster_sizes": sizes.tolist(),
    }
    return {
        "k": k,
        "centres": centres,
        "labels": labels,
        "coarse_labels": parent[labels],
        "parent": parent,
        "distance": distance,
        "relative_distance": relative.astype(np.float32),
        "profiles": profiles,
        "card": card,
    }


def assign(x: np.ndarray, centres: np.ndarray, parent: np.ndarray) -> tuple[np.ndarray, ...]:
    """Frozen assignment of new rows to existing centres (no refit)."""
    labels = np.empty(len(x), dtype=int)
    distance = np.empty(len(x), dtype=np.float32)
    for start in range(0, len(x), 20000):
        block = x[start : start + 20000]
        d = ((block[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        labels[start : start + 20000] = d.argmin(axis=1)
        distance[start : start + 20000] = np.sqrt(d.min(axis=1))
    return labels, parent[labels], distance

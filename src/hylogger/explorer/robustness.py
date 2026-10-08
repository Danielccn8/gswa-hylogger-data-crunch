"""Robustness of the interval clustering: transfer to unseen districts and interval width.

District transfer: whole districts (5 km groups) are withheld, clusters are
refitted on the rest with the same K, and the withheld intervals are assigned
to the refitted centres. Agreement (ARI) with the full-data clustering shows
whether the clusters are general or depend on particular districts.

Interval width: a random sample of holes is re-prepared at 0.5 m and 2 m.
For each width the K rule is re-applied and hole fingerprints are recomputed;
the Mantel-style Spearman correlation between hole distance matrices says
whether the whole-hole similarity picture depends on the 1 m choice.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import adjusted_rand_score

from . import clusters, fingerprints
from .data import IntervalMatrix, balanced_sample, model_features
from .features import MissingLogs, interval_features, select_dataset


def district_transfer(
    data: IntervalMatrix,
    k: int,
    district: dict[str, int],
    repeats: int = 3,
    share: float = 0.2,
    seed: int = 42,
) -> dict:
    x, _ = model_features(data)
    holes = data.meta.hole_id
    train_all = balanced_sample(holes, 150, seed)
    full = clusters._kmeans(x[train_all], k, seed)
    full_labels, _, _ = clusters.assign(x, full.cluster_centers_, np.arange(k))
    hole_district = holes.map(district).fillna(-1).to_numpy()
    rng = np.random.default_rng(seed)
    unique = np.unique(hole_district)
    rows = []
    for repeat in range(repeats):
        held_districts, covered = set(), 0
        for d in rng.permutation(unique):
            held_districts.add(d)
            covered = np.isin(hole_district, list(held_districts)).mean()
            if covered >= share:
                break
        held = np.isin(hole_district, list(held_districts))
        train = balanced_sample(holes[~held], 150, seed + repeat)
        train = np.flatnonzero(~held)[train] if len(train) else train
        model = clusters._kmeans(x[train], k, seed + repeat)
        labels, _, _ = clusters.assign(x[held], model.cluster_centers_, np.arange(k))
        rows.append(
            {
                "repeat": repeat,
                "held_districts": len(held_districts),
                "held_holes": int(holes[held].nunique()),
                "held_intervals": int(held.sum()),
                "ari_vs_full_fit": round(float(adjusted_rand_score(full_labels[held], labels)), 3),
            }
        )
    aris = [r["ari_vs_full_fit"] for r in rows]
    return {
        "k": k,
        "repeats": rows,
        "mean_ari": round(float(np.mean(aris)), 3),
        "min_ari": min(aris),
    }


def _matrix_from_tables(
    tables: list[pd.DataFrame], groups: dict[str, str], sensor: str
) -> IntervalMatrix:
    table = pd.concat(tables, ignore_index=True)
    table = table[table.n_admitted >= 3].reset_index(drop=True)
    minerals = sorted({m for item in table.minerals for m in item})
    index = {m: i for i, m in enumerate(minerals)}
    abundance = np.zeros((len(table), len(minerals)), dtype=np.float32)
    for row, item in enumerate(table.minerals):
        for mineral, value in item.items():
            abundance[row, index[mineral]] = value
    meta = table[
        ["feature_set_id", "bin", "hole_id", "depth_from_m", "depth_to_m", "n_total", "n_admitted"]
    ]
    return IntervalMatrix(sensor, meta, minerals, abundance, groups)


def _hole_distances(data: IntervalMatrix, seed: int) -> tuple[list[str], np.ndarray, int]:
    x, _ = model_features(data)
    train = balanced_sample(data.meta.hole_id, 150, seed)
    k = clusters.choose_k(clusters.score_k(x[train], clusters.FINE_K, seed))
    model = clusters._kmeans(x[train], k, seed)
    labels, _, _ = clusters.assign(x, model.cluster_centers_, np.arange(k))
    share = fingerprints.shares(data.meta.hole_id, labels, k)
    return share.index.tolist(), fingerprints.jensen_shannon(share.to_numpy()), k


def width_sensitivity(
    data_dir: Path,
    sensor: str,
    holes: list[str],
    widths: tuple[float, ...] = (0.5, 1.0, 2.0),
    seed: int = 42,
) -> dict:
    results: dict[float, tuple[list[str], np.ndarray, int]] = {}
    for width in widths:
        tables, groups = [], {}
        for hole_id in holes:
            source = select_dataset(data_dir / "downloads" / "processed" / hole_id)
            if source is None:
                continue
            try:
                table, report = interval_features(source, hole_id, sensor, width)
            except MissingLogs:
                continue
            tables.append(table)
            groups.update(report["mineral_groups"])
        if tables:
            results[width] = _hole_distances(_matrix_from_tables(tables, groups, sensor), seed)
    if 1.0 not in results:
        return {}
    names, base, base_k = results[1.0]
    out = {"holes": len(names), "k_by_width": {}, "mantel_vs_1m": {}}
    for width, (other_names, matrix, k) in results.items():
        out["k_by_width"][str(width)] = k
        if width == 1.0:
            continue
        common = [h for h in names if h in other_names]
        a = [names.index(h) for h in common]
        b = [other_names.index(h) for h in common]
        upper = np.triu_indices(len(common), 1)
        rho = spearmanr(base[np.ix_(a, a)][upper], matrix[np.ix_(b, b)][upper]).statistic
        out["mantel_vs_1m"][str(width)] = round(float(rho), 3)
    return out

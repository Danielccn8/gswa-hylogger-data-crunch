"""Bounded exploratory density comparison on development intervals only."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import HDBSCAN
from sklearn.metrics import silhouette_score

from hylogger.interval_model import matrix


def compare_hdbscan(table: pd.DataFrame, *, seed: int = 42, per_hole: int = 80) -> dict:
    """Measure separation and noise; do not choose a production model here."""
    valid = table.loc[table.n_admitted > 0].copy()
    holes = sorted(valid.hole_id.unique())
    if len(holes) < 3:
        raise ValueError("Three holes are required to keep one out of development")
    test_hole = holes[-1]
    development = valid.loc[valid.hole_id != test_hole]
    sampled = pd.concat(
        [
            part.sample(n=min(per_hole, len(part)), random_state=seed)
            for _, part in development.groupby("hole_id")
        ],
        ignore_index=True,
    )
    columns = sorted(set().union(*(features.keys() for features in sampled.features)))
    x = np.sqrt(matrix(sampled, columns))
    candidates = []
    for min_cluster_size in (20, 50, 100):
        labels = HDBSCAN(min_cluster_size=min_cluster_size, copy=True).fit_predict(x)
        covered = labels >= 0
        n_clusters = len(set(labels[covered]))
        separation = None
        if n_clusters >= 2 and covered.sum() > n_clusters:
            separation = float(
                silhouette_score(
                    x[covered],
                    labels[covered],
                    sample_size=min(int(covered.sum()), 1000),
                    random_state=seed,
                )
            )
        candidates.append(
            {
                "min_cluster_size": min_cluster_size,
                "clusters": n_clusters,
                "coverage": round(float(covered.mean()), 4),
                "noise_fraction": round(float((~covered).mean()), 4),
                "silhouette_non_noise": separation,
            }
        )
    return {
        "region": str(table.region.iloc[0]),
        "comparison": "HDBSCAN development sample; no deployed assignments",
        "test_hole_not_used": test_hole,
        "development_holes": len(holes) - 1,
        "sampled_intervals": len(sampled),
        "per_hole_cap": per_hole,
        "representation": "square root TSG group frequencies",
        "seed": seed,
        "candidates": candidates,
        "limitation": "Internal separation and noise are not geological correctness",
    }

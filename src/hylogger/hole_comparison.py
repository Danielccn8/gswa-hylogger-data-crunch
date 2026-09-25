"""Exploratory whole-hole comparison from versioned TSG interval products."""

from __future__ import annotations

import hashlib
import json
from math import ceil

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score


def compare_holes(
    intervals: pd.DataFrame, *, requested_holes: list[str], region: str, seed: int = 42
) -> dict:
    """Give every admitted depth bin equal weight, then cluster whole-hole summaries.

    Scores and review candidates are relative to this cohort. They are not
    geological anomaly probabilities or evidence of mineralisation.
    """
    if len(requested_holes) != len(set(requested_holes)):
        raise ValueError("Hole IDs must be unique")
    if region not in {"swir", "tir"}:
        raise ValueError("Region must be swir or tir")
    required = {"hole_id", "region", "interval_set_id", "n_admitted", "n_total", "features"}
    if not required.issubset(intervals.columns):
        raise ValueError(f"Missing interval columns: {sorted(required - set(intervals.columns))}")
    frame = intervals.loc[
        intervals.hole_id.isin(requested_holes) & intervals.region.eq(region)
    ].copy()
    if frame.empty:
        raise ValueError(f"No {region} intervals for the requested cohort")
    revisions = frame.groupby("hole_id").interval_set_id.nunique()
    if (revisions > 1).any():
        raise ValueError("Multiple interval-set revisions for a hole; select one per hole")

    admitted = frame.loc[frame.n_admitted > 0]
    columns = sorted(
        {
            name
            for features in admitted.features
            for name, value in features.items()
            if value is not None
        }
    )
    rows = []
    vectors = []
    for hole_id in requested_holes:
        part = frame.loc[frame.hole_id.eq(hole_id)]
        useful = part.loc[part.n_admitted > 0]
        if useful.empty:
            continue
        values = np.array(
            [[float(features.get(name) or 0) for name in columns] for features in useful.features],
            dtype=float,
        )
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"Invalid feature values in {hole_id}")
        vector = values.mean(axis=0)
        if not np.isclose(vector.sum(), 1, atol=1e-5):
            raise ValueError(f"Group frequencies do not sum to one in {hole_id}")
        vectors.append(vector)
        rows.append(
            {
                "hole_id": hole_id,
                "interval_set_id": str(part.interval_set_id.iloc[0]),
                "intervals_total": int(len(part)),
                "intervals_with_features": int(len(useful)),
                "readings_total": int(part.n_total.sum()),
                "readings_admitted": int(part.n_admitted.sum()),
                "group_frequencies": dict(zip(columns, vector.round(6).tolist(), strict=True)),
            }
        )
    if len(rows) < 4:
        raise ValueError("At least four holes with diagnostic intervals are required")
    x = np.sqrt(np.vstack(vectors))
    candidates = []
    for k in range(2, min(5, len(rows))):
        model = KMeans(n_clusters=k, n_init=20, random_state=seed).fit(x)
        if len(set(model.labels_)) < 2:
            continue
        repeated = KMeans(n_clusters=k, n_init=20, random_state=seed + 1).fit_predict(x)
        candidates.append(
            {
                "k": k,
                "silhouette": round(float(silhouette_score(x, model.labels_)), 4),
                "two_seed_ari": round(float(adjusted_rand_score(model.labels_, repeated)), 4),
            }
        )
    if not candidates:
        raise ValueError("Whole-hole summaries contain too little variation for clustering")
    selected = max(candidates, key=lambda item: (item["silhouette"], -item["k"]))
    model = KMeans(n_clusters=selected["k"], n_init=20, random_state=seed).fit(x)
    pairwise = np.linalg.norm(x[:, None, :] - x[None, :, :], axis=2)
    np.fill_diagonal(pairwise, np.inf)
    nearest = pairwise.min(axis=1)
    rank = sorted(range(len(rows)), key=lambda i: (-nearest[i], rows[i]["hole_id"]))
    review = set(rank[: max(1, ceil(len(rows) * 0.1))])
    for index, row in enumerate(rows):
        neighbor = int(pairwise[index].argmin())
        centroid = model.cluster_centers_[model.labels_[index]]
        row.update(
            cluster_id=int(model.labels_[index]),
            nearest_hole_id=rows[neighbor]["hole_id"],
            nearest_distance=round(float(nearest[index]), 6),
            centroid_distance=round(float(np.linalg.norm(x[index] - centroid)), 6),
            review_candidate=index in review,
        )
    groups = []
    original = np.vstack(vectors)
    for label in range(selected["k"]):
        members = np.flatnonzero(model.labels_ == label)
        mean = original[members].mean(axis=0)
        groups.append(
            {
                "cluster_id": label,
                "hole_count": int(len(members)),
                "leading_groups": [
                    {"name": columns[i], "mean_frequency": round(float(mean[i]), 4)}
                    for i in np.argsort(-mean)[:3]
                ],
            }
        )
    model_id = hashlib.sha256(
        json.dumps(
            {
                "sets": sorted(row["interval_set_id"] for row in rows),
                "region": region,
                "method": "equal-interval-mean-sqrt-kmeans-v1",
                "seed": seed,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:20]
    included_ids = {row["hole_id"] for row in rows}
    return {
        "model_id": model_id,
        "region": region,
        "row_unit": "one hole",
        "requested_holes": requested_holes,
        "included_holes": len(rows),
        "excluded_holes": [hole for hole in requested_holes if hole not in included_ids],
        "method": (
            "mean TSG group frequency across diagnostic 1 m intervals, "
            "equal interval weight; square-root transform; K-Means"
        ),
        "distance": "Euclidean distance on square-root group frequencies",
        "review_rule": "top 10% by distance to nearest other hole within this cohort",
        "limitation": (
            "Exploratory relative outlier screen only; small cohort, processed TSG "
            "without Final Mask, no independent geology validation."
        ),
        "feature_schema": columns,
        "candidate_k": candidates,
        "selected_k": selected["k"],
        "groups": groups,
        "holes": rows,
    }

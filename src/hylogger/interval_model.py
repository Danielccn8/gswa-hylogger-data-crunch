"""Fit and reuse a frozen TSG interval model, with hole-level holdout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score


def matrix(table: pd.DataFrame, columns: list[str]) -> np.ndarray:
    return np.asarray(
        [
            [row.get(name) if row.get(name) is not None else 0.0 for name in columns]
            for row in table.features
        ],
        dtype=float,
    )


def fit(
    table: pd.DataFrame, output: Path, *, k_values: tuple[int, ...] = (2, 3, 4), seed: int = 42
) -> dict:
    """Tune only on development holes; a third hole is needed for a real holdout."""
    valid = table[table.n_admitted > 0].copy().reset_index(drop=True)
    holes = sorted(valid.hole_id.unique())
    if len(holes) < 2:
        raise ValueError("At least two holes are needed for cross-hole evaluation")
    test_hole = holes[-1] if len(holes) >= 3 else None
    train = valid[valid.hole_id != test_hole] if test_hole else valid
    columns = sorted(set().union(*(item.keys() for item in train.features)))
    if not columns:
        raise ValueError("No diagnostic groups in training population")
    x = np.sqrt(matrix(train, columns))  # Hellinger geometry for proportions
    candidates = []
    for k in k_values:
        if k < 2 or k >= len(x):
            continue
        model = KMeans(n_clusters=k, random_state=seed, n_init=10).fit(x)
        if len(set(model.labels_)) < 2:
            continue
        sample = min(len(x), 2000)
        score = float(silhouette_score(x, model.labels_, sample_size=sample, random_state=seed))
        candidates.append({"k": k, "development_silhouette": score})
    if not candidates:
        raise ValueError("Too few distinct intervals for k comparison")
    chosen = max(candidates, key=lambda row: row["development_silhouette"])
    model = KMeans(n_clusters=chosen["k"], random_state=seed, n_init=10).fit(x)
    sample = min(len(x), 2000)
    direct = matrix(train, columns)
    direct_model = KMeans(n_clusters=chosen["k"], random_state=seed, n_init=10).fit(direct)
    direct_score = float(
        silhouette_score(direct, direct_model.labels_, sample_size=sample, random_state=seed)
    )
    pca = PCA(n_components=min(4, x.shape[1]), random_state=seed).fit(x)
    xp = pca.transform(x)
    pca_model = KMeans(n_clusters=chosen["k"], random_state=seed, n_init=10).fit(xp)
    pca_score = float(
        silhouette_score(xp, pca_model.labels_, sample_size=sample, random_state=seed)
    )
    seed_labels = [
        KMeans(n_clusters=chosen["k"], random_state=s, n_init=10).fit_predict(x)
        for s in (seed, seed + 1, seed + 2)
    ]
    seed_ari = float(
        np.mean([adjusted_rand_score(seed_labels[0], labels) for labels in seed_labels[1:]])
    )
    dependencies = {
        "scikit_learn": sklearn.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
    }
    run_hash = hashlib.sha256(
        (
            ":".join(sorted(table.interval_set_id.unique()))
            + json.dumps(chosen, sort_keys=True)
            + json.dumps(columns)
            + json.dumps(dependencies, sort_keys=True)
            + ":runner-v4"
        ).encode()
    ).hexdigest()[:20]
    output = output / run_hash
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"model": model, "columns": columns, "region": str(table.region.iloc[0])},
        output / "model.joblib",
    )
    compatible = valid.features.map(lambda item: set(item).issubset(columns))
    assignments = infer(valid[compatible].reset_index(drop=True), output)
    if (~compatible).any():
        rejected = pd.DataFrame(
            {
                "interval_id": valid.loc[~compatible, "interval_id"].tolist(),
                "model_id": run_hash,
                "cluster_id": None,
                "distance": None,
                "status": "out_of_schema",
            }
        )
        assignments = pd.concat([assignments, rejected], ignore_index=True)
    assignments.to_parquet(output / "assignments.parquet", index=False)
    heldout = valid[valid.hole_id == test_hole] if test_hole else valid.iloc[:0]
    heldout_assigned = heldout[
        heldout.interval_id.isin(assignments.loc[assignments.status == "assigned", "interval_id"])
    ]
    test_score = None
    if len(heldout_assigned) > chosen["k"]:
        hx = np.sqrt(matrix(heldout_assigned, columns))
        hlabels = model.predict(hx)
        if len(np.unique(hlabels)) > 1:
            test_score = float(
                silhouette_score(hx, hlabels, sample_size=min(len(hx), 2000), random_state=seed)
            )
    # Describe clusters in original TSG group-frequency units.
    profiles = []
    joined = valid.merge(assignments[assignments.status == "assigned"], on="interval_id")
    for cluster, part in joined.groupby("cluster_id"):
        raw = matrix(part, columns)
        profiles.append(
            {
                "cluster_id": int(cluster),
                "n_intervals": len(part),
                "mean_group_frequency": dict(zip(columns, raw.mean(axis=0).round(4), strict=True)),
            }
        )
    card = {
        "model_id": run_hash,
        "region": str(table.region.iloc[0]),
        "row_unit": "one depth interval",
        "representation": "square root of TSG group frequencies",
        "clustering": "K-Means",
        "pca": "not used",
        "seed": seed,
        "dependencies": dependencies,
        "feature_schema": columns,
        "development_holes": sorted(train.hole_id.unique().tolist()),
        "test_hole": test_hole,
        "candidate_scores": candidates,
        "selected_k": chosen["k"],
        "direct_frequency_development_silhouette_same_k": direct_score,
        "pca_development_silhouette_same_k": pca_score,
        "pca_variance_ratio_sum": float(pca.explained_variance_ratio_.sum()),
        "mean_ari_three_development_seeds": seed_ari,
        "test_silhouette": test_score,
        "test_note": (
            "Unlabelled test rows can be assigned; silhouette is not independent geology validation"
        ),
        "profiles": profiles,
        "n_assignments": int((assignments.status == "assigned").sum()),
        "n_out_of_schema": int((assignments.status == "out_of_schema").sum()),
        "limitation": (
            "TSG groups are not independent ground truth; Final Mask is absent "
            "in processed API logs."
        ),
    }
    (output / "model_card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    return card


def infer(table: pd.DataFrame, model_dir: Path) -> pd.DataFrame:
    """Assign using frozen schema and centroids. Retraining creates another model ID."""
    card_path = model_dir / "model_card.json"
    if card_path.exists():
        expected = json.loads(card_path.read_text(encoding="utf-8")).get("dependencies", {})
        actual = {
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        }
        if expected and expected != actual:
            raise ValueError(f"Frozen model dependency versions differ: {expected} != {actual}")
    bundle = joblib.load(model_dir / "model.joblib")
    if table.region.nunique() != 1 or table.region.iloc[0] != bundle["region"]:
        raise ValueError("Spectral region differs from frozen model")
    columns = bundle["columns"]
    unknown = set().union(*(item.keys() for item in table.features)) - set(columns)
    if unknown:
        raise ValueError(f"Unseen feature groups require review: {sorted(unknown)}")
    x = np.sqrt(matrix(table, columns))
    model = bundle["model"]
    labels = model.predict(x)
    distances = model.transform(x)[np.arange(len(x)), labels]
    return pd.DataFrame(
        {
            "interval_id": table.interval_id.tolist(),
            "model_id": model_dir.name,
            "cluster_id": labels.astype(int),
            "distance": distances.astype(float),
            "status": "assigned",
        }
    )

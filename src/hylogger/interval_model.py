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
    table: pd.DataFrame,
    output: Path,
    *,
    k_values: tuple[int, ...] = (2, 3, 4),
    seed: int = 42,
    test_holes: list[str] | None = None,
) -> dict:
    """Tune only on development holes; a third hole is needed for a real holdout."""
    valid = table[table.n_admitted > 0].copy().reset_index(drop=True)
    holes = sorted(valid.hole_id.unique())
    if len(holes) < 2:
        raise ValueError("At least two holes are needed for cross-hole evaluation")
    if table.region.nunique() != 1:
        raise ValueError("Fit one sensor region at a time")
    tests = (
        sorted(set(test_holes)) if test_holes is not None else holes[-1:] if len(holes) >= 3 else []
    )
    if set(tests) - set(holes):
        raise ValueError(
            f"Requested holdout holes lack diagnostic data: {sorted(set(tests) - set(holes))}"
        )
    train = valid[~valid.hole_id.isin(tests)]
    if train.hole_id.nunique() < 2:
        raise ValueError("At least two development holes must remain outside the holdout")
    test_hole = tests[0] if len(tests) == 1 else None
    columns = sorted(set().union(*(item.keys() for item in train.features)))
    if not columns:
        raise ValueError("No diagnostic groups in training population")
    x = np.sqrt(matrix(train, columns))  # Hellinger geometry for proportions
    weights = train.hole_id.map(1 / train.hole_id.value_counts()).to_numpy()
    weights = weights * (len(weights) / weights.sum())
    candidates = []
    for k in k_values:
        if k < 2 or k >= len(x):
            continue
        model = KMeans(n_clusters=k, random_state=seed, n_init=10).fit(x, sample_weight=weights)
        if len(set(model.labels_)) < 2:
            continue
        sample = min(len(x), 2000)
        score = float(silhouette_score(x, model.labels_, sample_size=sample, random_state=seed))
        candidates.append({"k": k, "development_silhouette": score})
    if not candidates:
        raise ValueError("Too few distinct intervals for k comparison")
    chosen = max(candidates, key=lambda row: row["development_silhouette"])
    model = KMeans(n_clusters=chosen["k"], random_state=seed, n_init=10).fit(
        x, sample_weight=weights
    )
    sample = min(len(x), 2000)
    direct = matrix(train, columns)
    direct_model = KMeans(n_clusters=chosen["k"], random_state=seed, n_init=10).fit(
        direct, sample_weight=weights
    )
    direct_score = float(
        silhouette_score(direct, direct_model.labels_, sample_size=sample, random_state=seed)
    )
    pca = PCA(n_components=min(4, x.shape[1], len(x)), random_state=seed).fit(x)
    xp = pca.transform(x)
    pca_model = KMeans(n_clusters=chosen["k"], random_state=seed, n_init=10).fit(
        xp, sample_weight=weights
    )
    pca_score = float(
        silhouette_score(xp, pca_model.labels_, sample_size=sample, random_state=seed)
    )
    seed_labels = [
        KMeans(n_clusters=chosen["k"], random_state=s, n_init=10)
        .fit(x, sample_weight=weights)
        .labels_
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
            + json.dumps({"test_holes": tests, "seed": seed, "k_values": k_values})
            + ":runner-v5-balanced-holes"
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
    heldout = valid[valid.hole_id.isin(tests)]
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
        "test_holes": tests,
        "split": "explicit whole-hole holdout"
        if test_holes is not None
        else "legacy alphabetical engineering split",
        "training_weighting": "equal total weight per development hole",
        "holdout_intervals": len(heldout),
        "holdout_assigned": len(heldout_assigned),
        "score_note": (
            "Silhouette is interval-weighted within its representation; "
            "do not compare geometries as geological accuracy"
        ),
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


def infer(
    table: pd.DataFrame, model_dir: Path, *, flag_out_of_schema: bool = False
) -> pd.DataFrame:
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
    observed = table.features.map(
        lambda item: {k for k, v in item.items() if v is not None and v != 0}
    )
    unknown = set().union(*observed) - set(columns)
    if unknown:
        if not flag_out_of_schema:
            raise ValueError(f"Unseen feature groups require review: {sorted(unknown)}")
        compatible = observed.map(lambda keys: keys.issubset(columns))
        rejected = pd.DataFrame(
            {
                "interval_id": table.loc[~compatible, "interval_id"].tolist(),
                "model_id": model_dir.name,
                "cluster_id": None,
                "distance": None,
                "status": "out_of_schema",
            }
        )
        accepted = (
            infer(table.loc[compatible].reset_index(drop=True), model_dir)
            if compatible.any()
            else rejected.iloc[:0]
        )
        return pd.concat([accepted, rejected], ignore_index=True)
    x = np.sqrt(matrix(table, columns))
    if not np.isfinite(x).all() or not np.allclose((x * x).sum(axis=1), 1, atol=1e-5):
        raise ValueError("Model input frequencies must be finite, non-negative and sum to one")
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

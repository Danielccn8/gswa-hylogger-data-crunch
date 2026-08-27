"""A small, explicit processed-log baseline: one observation per drill hole."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .config import AppConfig
from .errors import HyLoggerError
from .prepare import feature_file
from .selections import load_selection
from .util import atomic_write_frame, atomic_write_json, sha256_file, utc_now


@dataclass
class BaselineInputs:
    proportions: pd.DataFrame
    quality: pd.DataFrame
    feature_path: Path
    feature_log: str


@dataclass
class BaselineResult:
    assignments: pd.DataFrame
    model: Pipeline
    metrics: dict
    model_columns: list[str]


def load_baseline_inputs(
    config: AppConfig, selection: str, *, feature_log: str = "grp1_u_tsas"
) -> BaselineInputs:
    """Require one common, populated log; never impute an unavailable log."""
    selection = selection.lower()
    expected = list(load_selection(config, selection)["holes"])
    path = feature_file(config, f"selection_{selection}", source="processed")
    instruction = f"Run: hylogger prepare --selection {selection} --source processed"
    if not path.exists() or not path.with_suffix(".json").exists():
        raise HyLoggerError(f"No processed selection matrix found. {instruction}")
    status_path = path.with_suffix(".status.json")
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") != "complete":
            raise HyLoggerError(f"The last selection preparation did not complete. {instruction}")
    metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    features = pd.read_parquet(path)
    if (
        metadata.get("source") != "processed"
        or "feature_source" not in features
        or not features["feature_source"].eq("processed").all()
    ):
        raise HyLoggerError(f"This baseline accepts processed features only. {instruction}")
    if (
        features["hole_id"].duplicated().any()
        or set(features["hole_id"]) != set(expected)
        or set(metadata.get("hole_ids", [])) != set(expected)
        or sha256_file(path) != metadata.get("matrix_sha256")
    ):
        raise HyLoggerError(f"Selection or matrix changed since preparation. {instruction}")
    for hole_id in expected:
        input_path = feature_file(config, hole_id, source="processed")
        if not input_path.exists() or sha256_file(input_path) != metadata.get(
            "input_sha256", {}
        ).get(hole_id):
            raise HyLoggerError(f"Prepared features changed for {hole_id}. {instruction}")

    features = features.set_index("hole_id").loc[expected]
    prefix = f"processed__{feature_log}__"
    qc_prefix = f"qc__processed__{feature_log}__"
    quality = pd.DataFrame(index=features.index)
    for field in ("total_rows", "valid_rows", "missing_rows", "invalid_rows"):
        quality[field] = features.get(qc_prefix + field, np.nan)
    unusable = quality["valid_rows"].isna() | quality["valid_rows"].le(0)
    if unusable.any():
        raise HyLoggerError(
            f"{feature_log} is missing or has no valid labels for: "
            f"{', '.join(quality.index[unusable])}. Preview/fetch the same log for every "
            "hole, or remove those holes from this selection, then prepare again."
        )
    quality["valid_pct"] = 100 * quality["valid_rows"] / quality["total_rows"]
    quality["missing_pct"] = 100 * quality["missing_rows"] / quality["total_rows"]
    quality["invalid_pct"] = 100 * quality["invalid_rows"] / quality["total_rows"]

    columns = sorted(column for column in features if column.startswith(prefix))
    if not columns:
        raise HyLoggerError(f"No proportions were prepared for {feature_log}. {instruction}")
    # All holes have valid observations of this exact log. Missing category
    # columns now mean that category was not observed, not that its log is absent.
    proportions = features[columns].astype(float).fillna(0)
    proportions.columns = [column.removeprefix(prefix) for column in columns]
    if (
        not np.isfinite(proportions).all().all()
        or (proportions < 0).any().any()
        or (proportions > 1).any().any()
        or not np.allclose(proportions.sum(axis=1), 1)
    ):
        raise HyLoggerError(f"Invalid proportion matrix for {feature_log}. {instruction}")
    return BaselineInputs(proportions, quality, path, feature_log)


def fit_baseline(
    inputs: BaselineInputs,
    *,
    n_clusters: int = 2,
    random_state: int = 42,
    pca_variance: float | None = None,
) -> BaselineResult:
    """Standardise the group proportions and fit K-Means, optionally after PCA."""
    values = inputs.proportions
    if len(values) < 3 or not 2 <= n_clusters < len(values):
        raise HyLoggerError(
            f"Need at least 3 holes and 2 <= N_CLUSTERS < number of holes; "
            f"got {len(values)} holes and N_CLUSTERS={n_clusters}. "
            "A single hole can be inspected but cannot form a between-hole baseline."
        )
    if not np.isfinite(values).all().all():
        raise HyLoggerError("The baseline feature matrix contains non-finite values.")
    varying = values.columns[values.nunique() > 1].tolist()
    if not varying or len(values[varying].drop_duplicates()) < n_clusters:
        raise HyLoggerError("Too few distinct hole profiles for the requested cluster count.")
    steps = [("scale", StandardScaler())]
    if pca_variance is not None:
        if not 0 < pca_variance < 1:
            raise HyLoggerError("PCA_VARIANCE must be between 0 and 1, or None.")
        steps.append(("pca", PCA(n_components=pca_variance, svd_solver="full")))
    steps.append(("kmeans", KMeans(n_clusters=n_clusters, random_state=random_state, n_init=20)))
    model = Pipeline(steps)
    labels = model.fit_predict(values[varying])
    transformed = model[:-1].transform(values[varying])
    if len(np.unique(labels)) != n_clusters:
        raise HyLoggerError("K-Means collapsed clusters; reduce N_CLUSTERS or review the profiles.")
    score = float(silhouette_score(transformed, labels))
    assignments = pd.DataFrame({"hole_id": values.index, "cluster_id": labels})
    counts = assignments["cluster_id"].value_counts().sort_index()
    metrics = {
        "created_at": utc_now(),
        "source": "processed",
        "unit_of_clustering": "hole",
        "feature_log": inputs.feature_log,
        "feature_path": str(inputs.feature_path),
        "feature_sha256": sha256_file(inputs.feature_path),
        "holes": values.index.tolist(),
        "n_holes": len(values),
        "n_features": len(varying),
        "algorithm": "KMeans",
        "n_clusters": n_clusters,
        "random_state": random_state,
        "n_init": 20,
        "scaling": "StandardScaler",
        "pca_variance": pca_variance,
        "pca_components": int(model.named_steps["pca"].n_components_)
        if pca_variance is not None
        else None,
        "silhouette_score": score,
        "cluster_sizes": {str(key): int(value) for key, value in counts.items()},
        "has_singleton_cluster": bool(counts.eq(1).any()),
        "dropped_constant_columns": sorted(set(values.columns) - set(varying)),
        "interpretation": "Exploratory hole-profile similarity, not a gold prediction model.",
    }
    return BaselineResult(assignments, model, metrics, varying)


def save_baseline_run(
    config: AppConfig, inputs: BaselineInputs, result: BaselineResult, *, selection: str
) -> Path:
    """Keep every run separate; downloaded data and previous runs are not overwritten."""
    from datetime import UTC, datetime

    import sklearn

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
    folder = config.repo_root / "experiments" / "tsg_baseline" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    atomic_write_frame(result.assignments, folder / "assignments.csv")
    atomic_write_frame(inputs.proportions.reset_index(), folder / "proportions.csv")
    atomic_write_frame(inputs.quality.reset_index(), folder / "quality.csv")
    metrics = {
        **result.metrics,
        "selection": selection,
        "model_columns": result.model_columns,
        "scikit_learn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
    }
    atomic_write_json(folder / "run.json", metrics)
    return folder

"""Whole-hole clustering from raw spectra summaries (not just depth sections).

Interval median spectra from every summarised hole are continuum-removed (NIR)
or SNV-normalised (TIR), then projected with a PCA fitted on a hole-balanced
sample. Two whole-hole views follow:
- distribution distance: per-component Wasserstein distance between holes'
  interval score distributions (Yeshwanth's raw_data_PCA idea, now on QC'd,
  mask-admitted 1 m medians);
- spectral-class shares: interval spectral clusters, then JS distance on each
  hole's class shares (as for processed fingerprints).
Agreement with the processed-log view is reported as the Spearman correlation
between the two distance matrices on the shared holes (a Mantel-style check).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.decomposition import PCA

from ..intervals import continuum_removal
from . import clusters, fingerprints
from .data import balanced_sample
from .raw_summary import load_spectra

RAW_K = (4, 5, 6, 8, 10, 12)


def preprocess(spectra: np.ndarray, wavelengths: np.ndarray, sensor: str) -> np.ndarray:
    spectra = np.nan_to_num(spectra.astype(np.float64), nan=0.0)
    if sensor == "nir":
        return continuum_removal(np.clip(spectra, 1e-6, None), wavelengths)
    mean = spectra.mean(axis=1, keepdims=True)
    std = spectra.std(axis=1, keepdims=True)
    return (spectra - mean) / np.where(std > 0, std, 1)


def load_sensor(products: pd.DataFrame, sensor: str) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    tables, blocks, grid = [], [], None
    for row in products.itertuples(index=False):
        path = Path(row.product_path)
        if not (path / f"raw_{sensor}.npz").exists():
            continue
        table, wavelengths, spectra = load_spectra(path, sensor)
        if grid is None:
            grid = wavelengths
        elif len(wavelengths) != len(grid) or not np.allclose(wavelengths, grid):
            continue  # incompatible grid: excluded rather than interpolated twice
        tables.append(table)
        blocks.append(spectra)
    if not tables:
        return pd.DataFrame(), np.empty((0, 0)), np.empty(0)
    return pd.concat(tables, ignore_index=True), np.vstack(blocks), grid


def analyse(
    products: pd.DataFrame, sensor: str, processed_distance: dict | None = None, seed: int = 42
) -> dict | None:
    meta, spectra, grid = load_sensor(products, sensor)
    if meta.empty or meta.hole_id.nunique() < 2:
        return None
    features = preprocess(spectra, grid, sensor)
    train = balanced_sample(meta.hole_id, 300, seed)
    pca = PCA(n_components=0.95, random_state=seed).fit(features[train])
    if pca.n_components_ > 12:
        pca = PCA(n_components=12, random_state=seed).fit(features[train])
    scores = pca.transform(features).astype(np.float32)
    weights = pca.explained_variance_ratio_ / pca.explained_variance_ratio_.sum()

    # Interval spectral classes (K rule as for processed intervals).
    table = clusters.score_k(scores[train], tuple(k for k in RAW_K if k < len(train)), seed)
    k = clusters.choose_k(table)
    model = clusters._kmeans(scores[train], k, seed)
    order = np.argsort(-np.bincount(model.labels_, minlength=k))
    centres = model.cluster_centers_[order]
    labels, _, distance = clusters.assign(scores, centres, np.arange(k))

    names, profiles = fingerprints.quantile_profiles(meta.hole_id, scores)
    wasserstein = fingerprints.wasserstein_matrix(profiles, weights)
    share = fingerprints.shares(meta.hole_id, labels, k).reindex(names)
    js = fingerprints.jensen_shannon(share.to_numpy())
    hole_labels, hole_card, order_leaves = fingerprints.cluster_holes(wasserstein, seed)
    embedding = fingerprints.embed(wasserstein, seed)

    band_columns = [c for c in meta.columns if c.endswith(("_pos", "_depth", "_height"))] + [
        "albedo"
    ]
    class_profiles = []
    for cluster in range(k):
        members = labels == cluster
        class_profiles.append(
            {
                "cluster_id": cluster,
                "n_intervals": int(members.sum()),
                "n_holes": int(meta.hole_id[members].nunique()),
                "mean_spectrum": spectra[members].mean(axis=0).round(4).tolist(),
                "bands": {c: _finite(meta.loc[members, c].median()) for c in band_columns},
            }
        )
    agreement = None
    if processed_distance:
        common = [h for h in names if h in processed_distance["names"]]
        if len(common) >= 5:
            a = [names.index(h) for h in common]
            b = [processed_distance["names"].index(h) for h in common]
            upper = np.triu_indices(len(common), 1)
            raw_d = wasserstein[np.ix_(a, a)][upper]
            proc_d = processed_distance["matrix"][np.ix_(b, b)][upper]
            agreement = {
                "holes": len(common),
                "spearman_rho": round(float(spearmanr(raw_d, proc_d).statistic), 3),
                "against": processed_distance["label"],
            }
    return {
        "sensor": sensor,
        "meta": meta.assign(spectral_class=labels, class_distance=distance),
        "grid": grid,
        "names": names,
        "wasserstein": wasserstein,
        "js": js,
        "shares": share,
        "hole_labels": hole_labels,
        "embedding": embedding,
        "leaf_order": order_leaves,
        "class_profiles": class_profiles,
        "card": {
            "preprocessing": "continuum removal (hull quotient)" if sensor == "nir" else "SNV",
            "pca_components": int(pca.n_components_),
            "pca_variance": round(float(pca.explained_variance_ratio_.sum()), 4),
            "interval_classes": k,
            "k_scores": table.to_dict("records"),
            "hole_clustering": hole_card,
            "distance": "variance-weighted per-component Wasserstein (50 quantiles)",
            "agreement_with_processed": agreement,
            "holes": len(names),
            "intervals": int(len(meta)),
            "wavelengths_nm": [float(grid[0]), float(grid[-1])],
        },
    }


def _finite(value: float) -> float | None:
    return float(value) if value is not None and np.isfinite(value) else None

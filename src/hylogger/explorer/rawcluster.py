"""Whole-hole clustering from raw spectra summaries (not just depth sections).

Interval median spectra from every summarised hole are continuum-removed (NIR)
or SNV-normalised (TIR), then projected with a PCA fitted on a hole-balanced
sample. Two whole-hole views follow:
- joint distribution distance: deterministic sliced Wasserstein between holes'
  interval score distributions; the former variance-weighted marginal distance
  remains an explicitly named legacy comparator;
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

RAW_K = (2, 3, 4, 5, 6, 8, 10, 12)
MODEL_VERSION = "raw-joint-distribution-v2"


def preprocess(spectra: np.ndarray, wavelengths: np.ndarray, sensor: str) -> np.ndarray:
    spectra = spectra.astype(np.float64)
    if not np.isfinite(spectra).all():
        raise ValueError("Raw preprocessing requires finite admitted spectra")
    if sensor == "nir":
        return continuum_removal(np.clip(spectra, 1e-6, None), wavelengths)
    mean = spectra.mean(axis=1, keepdims=True)
    std = spectra.std(axis=1, keepdims=True)
    return (spectra - mean) / np.where(std > 0, std, 1)


def load_sensor(products: pd.DataFrame, sensor: str) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    tables, blocks, grid = [], [], None
    excluded_products = []
    for row in products.itertuples(index=False):
        path = Path(row.product_path)
        if not (path / f"raw_{sensor}.npz").exists():
            excluded_products.append(
                {"hole_id": str(row.hole_id), "reason": "missing_spectral_summary"}
            )
            continue
        table, wavelengths, spectra = load_spectra(path, sensor)
        if grid is None:
            grid = wavelengths
        elif len(wavelengths) != len(grid) or not np.allclose(wavelengths, grid):
            excluded_products.append(
                {"hole_id": str(row.hole_id), "reason": "incompatible_wavelength_grid"}
            )
            continue  # incompatible grid: excluded rather than interpolated twice
        tables.append(table)
        blocks.append(spectra)
    if not tables:
        return pd.DataFrame(), np.empty((0, 0)), np.empty(0)
    meta = pd.concat(tables, ignore_index=True)
    meta.attrs["load_report"] = {
        "products_requested": len(products),
        "products_loaded": len(tables),
        "products_excluded": len(excluded_products),
        "excluded_products": excluded_products,
    }
    return meta, np.vstack(blocks), grid


def supported_intervals(
    meta: pd.DataFrame, spectra: np.ndarray, minimum: int = fingerprints.MIN_INTERVALS
) -> tuple[pd.DataFrame, np.ndarray, dict]:
    """Apply the fingerprint support floor before every raw fitting stage."""
    finite = np.isfinite(spectra).all(axis=1)
    total_counts = meta.groupby("hole_id").size()
    counts = meta.loc[finite].groupby("hole_id").size().reindex(total_counts.index, fill_value=0)
    admitted = counts[counts >= minimum].index
    keep = finite & meta.hole_id.isin(admitted).to_numpy()
    exclusions = [
        {
            "hole_id": str(h),
            "intervals_loaded": int(total_counts[h]),
            "finite_intervals": int(counts[h]),
            "reason": "insufficient_finite_interval_support",
        }
        for h in counts.index
        if counts[h] < minimum
    ]
    report = {
        "minimum_intervals_per_hole": minimum,
        "holes_loaded": len(counts),
        "holes_eligible": len(admitted),
        "holes_excluded": len(exclusions),
        "intervals_loaded": len(meta),
        "intervals_eligible": int(keep.sum()),
        "nonfinite_intervals_excluded": int((~finite).sum()),
        "excluded_holes": exclusions,
        "support_note": (
            f"{minimum} finite 1 m intervals is a compatibility floor, not a confidence guarantee; "
            "depth coverage may remain unequal"
        ),
        "loading": meta.attrs.get("load_report", {}),
    }
    return meta.loc[keep].reset_index(drop=True), spectra[keep], report


def analyse(
    products: pd.DataFrame, sensor: str, processed_distance: dict | None = None, seed: int = 42
) -> dict | None:
    meta, spectra, grid = load_sensor(products, sensor)
    if meta.empty:
        return None
    meta, spectra, support = supported_intervals(meta, spectra)
    if meta.hole_id.nunique() < 2:
        raise clusters.NoValidClusteringError(
            f"raw {sensor}: fewer than two support-eligible holes "
            f"({support['holes_eligible']}/{support['holes_loaded']}; "
            f"minimum {fingerprints.MIN_INTERVALS} finite intervals per hole)",
            details=support,
        )
    features = preprocess(spectra, grid, sensor)
    train = balanced_sample(meta.hole_id, 300, seed)
    if not np.isfinite(features).all() or np.var(features[train], axis=0).sum() <= 1e-15:
        raise clusters.NoValidClusteringError(
            f"raw {sensor}: no finite spectral variation for PCA", details=support
        )
    pca = PCA(n_components=0.95, random_state=seed).fit(features[train])
    if pca.n_components_ > 12:
        pca = PCA(n_components=12, random_state=seed).fit(features[train])
    scores = pca.transform(features).astype(np.float32)
    weights = pca.explained_variance_ratio_ / pca.explained_variance_ratio_.sum()

    # Interval spectral classes (K rule as for processed intervals).
    table = clusters.score_k(scores[train], tuple(k for k in RAW_K if k < len(train)), seed)
    k = clusters.choose_k(table)
    model = clusters._kmeans(scores[train], k, seed)
    if len(np.unique(model.labels_)) != k:
        raise clusters.NoValidClusteringError(
            f"raw {sensor}: final spectral-class fit collapsed selected K", table
        )
    if np.bincount(model.labels_, minlength=k).min() / len(train) < clusters.MIN_SHARE:
        raise clusters.NoValidClusteringError(
            f"raw {sensor}: final spectral-class fit failed minimum cluster share", table
        )
    order = np.argsort(-np.bincount(model.labels_, minlength=k))
    centres = model.cluster_centers_[order]
    labels, _, distance = clusters.assign(scores, centres, np.arange(k))

    names, wasserstein, distance_card = fingerprints.sliced_wasserstein(meta.hole_id, scores)
    legacy_names, profiles = fingerprints.quantile_profiles(meta.hole_id, scores)
    if names != legacy_names:
        raise ValueError("Raw distribution comparators have misaligned holes")
    legacy_wasserstein = fingerprints.wasserstein_matrix(profiles, weights)
    share = fingerprints.shares(meta.hole_id, labels, k).reindex(names)
    js = fingerprints.jensen_shannon(share.to_numpy())
    hole_labels, hole_card, order_leaves = fingerprints.cluster_holes(wasserstein, seed)
    embedding = fingerprints.embed(wasserstein, seed)

    band_columns = [
        c for c in meta.columns if c.endswith(("_pos", "_depth", "_height")) or c == "albedo"
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
                "spearman_rho": _finite(round(float(spearmanr(raw_d, proc_d).statistic), 3)),
                "against": processed_distance["label"],
            }
    return {
        "sensor": sensor,
        "meta": meta.assign(spectral_class=labels, class_distance=distance),
        "grid": grid,
        "names": names,
        "wasserstein": wasserstein,
        "legacy_wasserstein": legacy_wasserstein,
        "js": js,
        "shares": share,
        "hole_labels": hole_labels,
        "embedding": embedding,
        "leaf_order": order_leaves,
        "class_profiles": class_profiles,
        "card": {
            "model_version": MODEL_VERSION,
            **distance_card,
            "support": support,
            "preprocessing": "continuum removal (hull quotient)" if sensor == "nir" else "SNV",
            "pca_components": int(pca.n_components_),
            "pca_variance": round(float(pca.explained_variance_ratio_.sum()), 4),
            "pca_requested_variance": 0.95,
            "pca_component_cap": 12,
            "pca_variance_target_met": bool(pca.explained_variance_ratio_.sum() >= 0.95 - 1e-8),
            "interval_classes": k,
            "final_min_share": float(np.bincount(model.labels_, minlength=k).min() / len(train)),
            "k_scores": table.to_dict("records"),
            "hole_clustering": hole_card,
            "legacy_comparator": {
                "distance_version": "marginal-variance-weighted-wasserstein-v1",
                "distance": "variance-weighted per-component Wasserstein (50 quantiles)",
                "note": (
                    "same cohort/PCA; marginal comparator ignores joint dependence and "
                    "adds variance weights to unwhitened scores"
                ),
            },
            "agreement_with_processed": agreement,
            "holes": len(names),
            "intervals": int(len(meta)),
            "wavelengths_nm": [float(grid[0]), float(grid[-1])],
        },
    }


def _finite(value: float) -> float | None:
    return float(value) if value is not None and np.isfinite(value) else None

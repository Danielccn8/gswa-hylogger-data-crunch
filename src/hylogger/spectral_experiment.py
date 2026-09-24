"""Bounded TSG-package spectral contrast, separate from interpretation models."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from .config import AppConfig
from .intervals import build_intervals
from .raw import find_package_dir
from .util import sha256_file


def run_spectral_contrast(
    config: AppConfig, holes: list[str], output: Path, region: str, seed: int = 42
) -> dict:
    """Fit on the first hole, assign a second; this is an engineering pilot."""
    if len(holes) != 2 or region not in ("swir", "tir"):
        raise ValueError("The bounded pilot requires two holes and one sensor region")
    tables = [
        build_intervals(config, hole, region=region, interval_m=1, remove_continuum=False)
        for hole in holes
    ]
    if tables[0].wavelengths.shape != tables[1].wavelengths.shape or not np.allclose(
        tables[0].wavelengths, tables[1].wavelengths
    ):
        raise ValueError("Different wavelength grids; resampling requires a reviewed policy")

    # Standard normal variate removes row brightness. All transformations fit
    # exclusively to the development hole and are frozen for the second hole.
    def snv(spectra: np.ndarray) -> np.ndarray:
        mean = spectra.mean(axis=1, keepdims=True)
        scale = spectra.std(axis=1, keepdims=True)
        if np.any(scale <= 0) or not np.isfinite(scale).all():
            raise ValueError("Flat or invalid interval spectrum")
        return (spectra - mean) / scale

    train = snv(tables[0].spectra)
    test = snv(tables[1].spectra)
    scaler = StandardScaler().fit(train)
    train_scaled = scaler.transform(train)
    test_scaled = scaler.transform(test)
    pca = PCA(n_components=0.95, svd_solver="full", random_state=seed).fit(train_scaled)
    x = pca.transform(train_scaled)
    y = pca.transform(test_scaled)
    candidates = []
    for k in (2, 3, 4):
        if k >= len(x):
            continue
        model = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(x)
        score = float(
            silhouette_score(x, model.labels_, sample_size=min(len(x), 500), random_state=seed)
        )
        candidates.append({"k": k, "development_silhouette": score})
    selected = max(candidates, key=lambda item: item["development_silhouette"])
    model = KMeans(n_clusters=selected["k"], n_init=10, random_state=seed).fit(x)
    labels = model.predict(y)
    train_distance = model.transform(x)[np.arange(len(x)), model.labels_]
    threshold = float(np.quantile(train_distance, 0.99))
    distances = model.transform(y)[np.arange(len(y)), labels]
    status = np.where(distances <= threshold, "assigned", "outside_training_distance")
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "scaler": scaler,
            "pca": pca,
            "kmeans": model,
            "wavelengths": tables[0].wavelengths,
            "region": region,
        },
        output / "model.joblib",
    )
    inference = (
        tables[1].table[["hole_id", "region", "depth_from", "depth_to", "source_rows"]].copy()
    )
    inference["cluster_id"] = labels
    inference["centroid_distance"] = distances
    inference["status"] = status
    inference.to_json(output / "inference.json", orient="records", indent=2)
    means = []
    for cluster in range(selected["k"]):
        means.append(tables[0].spectra_raw[model.labels_ == cluster].mean(axis=0))
    np.savez_compressed(
        output / "cluster_mean_spectra.npz",
        wavelengths=tables[0].wavelengths,
        mean_spectra=np.vstack(means),
    )
    indices = np.linspace(
        0, len(tables[0].wavelengths) - 1, min(100, len(tables[0].wavelengths)), dtype=int
    )
    spectral_view = {
        "wavelengths": tables[0].wavelengths[indices].round(3).tolist(),
        "cluster_mean": {
            str(i): mean[indices].round(6).tolist() for i, mean in enumerate(means)
        },
        "observations": [
            {
                "depth_from_m": float(row.depth_from),
                "depth_to_m": float(row.depth_to),
                "source_rows": row.source_rows,
                "cluster_id": int(labels[i]),
                "distance": float(distances[i]),
                "status": str(status[i]),
                "spectrum": tables[1].spectra_raw[i, indices].round(6).tolist(),
            }
            for i, row in enumerate(tables[1].table.itertuples())
        ],
    }
    (output / "spectral_view.json").write_text(
        json.dumps(spectral_view), encoding="utf-8"
    )
    source_files = {}
    for hole in holes:
        package_dir = find_package_dir(config, hole)
        suffix = "_tsg" if region == "swir" else "_tsg_tir"
        files = sorted(
            p for p in package_dir.iterdir()
            if p.stem.endswith(suffix) and p.suffix.lower() in {".tsg", ".bip"}
        )
        source_files[hole] = [
            {"name": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)}
            for p in files
        ]
    card = {
        "status": "engineering pilot, not geological validation",
        "region": region,
        "development_hole": holes[0],
        "inference_hole": holes[1],
        "seed": seed,
        "row_unit": "1 m median adjusted TSG-package spectrum from Final Mask-admitted readings",
        "transforms": "SNV per interval, training-only band scaling, training-only PCA",
        "pca_components": int(pca.n_components_),
        "variance_retained": float(sum(pca.explained_variance_ratio_)),
        "candidate_scores": candidates,
        "selected_k": selected["k"],
        "training_distance_99pct": threshold,
        "outside_training_distance": int((status != "assigned").sum()),
        "inference_cluster_counts": {
            str(k): int(v) for k, v in zip(*np.unique(labels, return_counts=True), strict=True)
        },
        "quality": {table.hole_id: table.qc for table in tables},
        "source_files": source_files,
        "wavelength_min": float(tables[0].wavelengths.min()),
        "wavelength_max": float(tables[0].wavelengths.max()),
        "bands": len(tables[0].wavelengths),
        "limitation": (
            "Two holes cannot establish generalisation. TSG labels remain "
            "contextual, not independent truth."
        ),
    }
    (output / "model_card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    return card

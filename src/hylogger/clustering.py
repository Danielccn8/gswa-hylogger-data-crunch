from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import (
    adjusted_rand_score,
    calinski_harabasz_score,
    davies_bouldin_score,
    silhouette_score,
)

from .config import AppConfig
from .errors import HyLoggerError
from .raw import find_package_dir

DEFAULT_SCALAR_COLUMNS = (
    "Min1_sTSAS",
    "Grp1_sTSAS",
    "Min1_uTSAS",
    "Grp1_uTSAS",
)


@dataclass
class HoleSpectra:
    hole_id: str
    wavelengths: np.ndarray
    spectra: np.ndarray
    metadata: pd.DataFrame
    scalar_columns: frozenset[str] = field(default_factory=frozenset)


@dataclass
class MultiHoleClusteringResult:
    assignments: pd.DataFrame
    cluster_sizes: pd.DataFrame
    model: KMeans
    pca: PCA
    wavelengths: np.ndarray
    mean_snv_spectra: np.ndarray
    k_scores: pd.DataFrame


def load_hole_spectra(config: AppConfig, hole_id: str) -> HoleSpectra:
    """Load finite NIR/SWIR spectra and provenance for one downloaded raw hole."""
    hole_id = hole_id.upper()
    package_dir = find_package_dir(config, hole_id)
    if package_dir is None:
        raise HyLoggerError(
            f"No extracted raw package is available for {hole_id}. "
            f"Run: hylogger raw fetch {hole_id} --components spectra"
        )

    from pytsg import parse_tsg

    package = parse_tsg.read_package(package_dir, read_cras_file=False)
    nir = package.nir
    spectra = np.asarray(nir.spectra, dtype=np.float32)
    wavelengths = np.asarray(nir.wavelength, dtype=np.float64)
    headers = nir.sampleheaders.reset_index(drop=True)
    scalars = nir.scalars.reset_index(drop=True)

    keep = np.ones(len(spectra), dtype=bool)
    keep &= np.isfinite(spectra).all(axis=1)

    def header(name: str, default: Any = pd.NA) -> np.ndarray:
        if name in headers.columns:
            return headers[name].to_numpy()[keep]
        return np.full(int(keep.sum()), default, dtype=object)

    metadata = pd.DataFrame(
        {
            "hole_id": hole_id,
            "depth_m": pd.to_numeric(header("D"), errors="coerce"),
            "tray": header("T"),
            "source_row": np.flatnonzero(keep),
        }
    )
    available_scalars: set[str] = set()
    normalised_names = {_normalise_scalar_name(name): name for name in scalars.columns}
    for canonical_name in DEFAULT_SCALAR_COLUMNS:
        source_name = normalised_names.get(_normalise_scalar_name(canonical_name))
        if source_name is not None:
            metadata[canonical_name] = scalars[source_name].to_numpy()[keep]
            available_scalars.add(canonical_name)
    return HoleSpectra(
        hole_id, wavelengths, spectra[keep], metadata, frozenset(available_scalars)
    )


def load_holes_with_scalar_check(
    hole_ids: Iterable[str],
    *,
    required_columns: Iterable[str] = DEFAULT_SCALAR_COLUMNS,
    config: AppConfig | None = None,
) -> tuple[list[HoleSpectra], pd.DataFrame]:
    """Load holes and return only those containing every required scalar column."""
    config = config or AppConfig.load()
    required = tuple(required_columns)
    unknown = sorted(set(required) - set(DEFAULT_SCALAR_COLUMNS))
    if unknown:
        raise HyLoggerError(f"Unknown scalar columns requested: {', '.join(unknown)}")

    eligible: list[HoleSpectra] = []
    rows: list[dict[str, Any]] = []
    for raw_hole_id in hole_ids:
        hole_id = raw_hole_id.upper().removesuffix(".ZIP")
        row: dict[str, Any] = {"hole_id": hole_id, "load_error": None}
        try:
            hole = load_hole_spectra(config, hole_id)
        except (HyLoggerError, FileNotFoundError, StopIteration) as error:
            row.update({column: False for column in required})
            row["eligible"] = False
            row["load_error"] = str(error)
        else:
            row.update({column: column in hole.scalar_columns for column in required})
            row["eligible"] = all(row[column] for column in required)
            if row["eligible"]:
                eligible.append(hole)
        rows.append(row)
    return eligible, pd.DataFrame(rows)


def cluster_holes(
    holes: Iterable[HoleSpectra],
    *,
    n_clusters: int | None = None,
    k_values: Iterable[int] = range(2, 9),
    variance_retained: float = 0.95,
    training_sample_size: int | None = None,
    silhouette_sample_size: int = 5_000,
    stability_repeats: int = 3,
    minimum_cluster_percentage: float = 1.0,
    minimum_hole_coverage: float = 0.5,
    random_state: int = 42,
) -> MultiHoleClusteringResult:
    """Fit one scalable spectral clustering model across several holes."""
    loaded = list(holes)
    if len(loaded) < 2:
        raise HyLoggerError("Multi-hole clustering requires at least two loaded holes.")
    if n_clusters is not None and n_clusters < 2:
        raise HyLoggerError("n_clusters must be at least 2.")

    wavelengths = _common_wavelengths(loaded)
    spectra_parts: list[np.ndarray] = []
    metadata_parts: list[pd.DataFrame] = []
    for hole in loaded:
        spectra_parts.append(_resample(hole.spectra, hole.wavelengths, wavelengths))
        metadata_parts.append(hole.metadata)

    spectra = np.vstack(spectra_parts)
    metadata = pd.concat(metadata_parts, ignore_index=True)
    snv, valid = _snv(spectra)
    metadata = metadata.loc[valid].reset_index(drop=True)
    requested_clusters = [n_clusters] if n_clusters is not None else sorted(set(k_values))
    if not requested_clusters or requested_clusters[0] < 2:
        raise HyLoggerError("k_values must contain cluster counts of at least 2.")
    if len(snv) < max(requested_clusters):
        raise HyLoggerError(f"Only {len(snv)} valid spectra remain for {n_clusters} clusters.")

    if training_sample_size is None or training_sample_size >= len(snv):
        training = snv
    else:
        rng = np.random.default_rng(random_state)
        training_rows = np.sort(
            rng.choice(len(snv), size=training_sample_size, replace=False)
        )
        training = snv[training_rows]

    pca = PCA(n_components=variance_retained, svd_solver="full")
    pca.fit(training)
    spectra_pca = pca.transform(snv)

    diagnostic_rows = _balanced_sample_indices(
        metadata, maximum_rows=silhouette_sample_size, random_state=random_state
    )
    diagnostic_pca = spectra_pca[diagnostic_rows]
    diagnostic_holes = metadata.iloc[diagnostic_rows]["hole_id"].to_numpy()

    scores: list[dict[str, float | int]] = []
    candidates: dict[int, tuple[KMeans, np.ndarray]] = {}
    for k in requested_clusters:
        candidate = KMeans(n_clusters=k, random_state=random_state, n_init=20)
        candidate_labels = candidate.fit_predict(spectra_pca)
        diagnostic_labels = candidate_labels[diagnostic_rows]
        counts = np.bincount(candidate_labels, minlength=k)
        hole_coverage = [
            len(np.unique(diagnostic_holes[diagnostic_labels == cluster]))
            / len(np.unique(diagnostic_holes))
            for cluster in range(k)
        ]
        stability = _cluster_stability(
            diagnostic_pca, k=k, repeats=stability_repeats, random_state=random_state
        )
        scores.append(
            {
                "k": k,
                "inertia": candidate.inertia_,
                "silhouette_score": silhouette_score(diagnostic_pca, diagnostic_labels),
                "calinski_harabasz_score": calinski_harabasz_score(
                    diagnostic_pca, diagnostic_labels
                ),
                "davies_bouldin_score": davies_bouldin_score(
                    diagnostic_pca, diagnostic_labels
                ),
                "stability_ari": stability,
                "minimum_cluster_percentage": float(counts.min() / len(candidate_labels) * 100),
                "minimum_hole_coverage": float(min(hole_coverage)),
            }
        )
        candidates[k] = (candidate, candidate_labels)
    k_scores = _rank_k_scores(
        pd.DataFrame(scores),
        minimum_cluster_percentage=minimum_cluster_percentage,
        minimum_hole_coverage=minimum_hole_coverage,
    )
    selected_k = (
        n_clusters
        if n_clusters is not None
        else int(k_scores.loc[k_scores["consensus_score"].idxmax(), "k"])
    )
    k_scores["selected"] = k_scores["k"].eq(selected_k)
    model, labels = candidates[selected_k]

    assignments = metadata.copy()
    assignments["cluster_id"] = labels
    cluster_sizes = (
        assignments.groupby(["hole_id", "cluster_id"], as_index=False)
        .size()
        .rename(columns={"size": "spectra_count"})
    )
    totals = cluster_sizes.groupby("hole_id")["spectra_count"].transform("sum")
    cluster_sizes["percentage"] = (cluster_sizes["spectra_count"] / totals * 100).round(2)
    mean_spectra = np.vstack(
        [snv[labels == label].mean(axis=0) for label in range(selected_k)]
    )
    return MultiHoleClusteringResult(
        assignments, cluster_sizes, model, pca, wavelengths, mean_spectra, k_scores
    )


def run_multi_hole_clustering(
    hole_ids: Iterable[str],
    *,
    config: AppConfig | None = None,
    **kwargs: Any,
) -> MultiHoleClusteringResult:
    config = config or AppConfig.load()
    ids = [hole_id.upper() for hole_id in hole_ids]
    if len(ids) != len(set(ids)):
        raise HyLoggerError("Each hole ID must appear only once.")
    return cluster_holes([load_hole_spectra(config, hole_id) for hole_id in ids], **kwargs)


def _common_wavelengths(holes: list[HoleSpectra]) -> np.ndarray:
    lower = max(float(hole.wavelengths.min()) for hole in holes)
    upper = min(float(hole.wavelengths.max()) for hole in holes)
    reference = holes[0].wavelengths
    common = reference[(reference >= lower) & (reference <= upper)]
    if len(common) < 2:
        raise HyLoggerError("The selected holes have no common NIR/SWIR wavelength range.")
    return common


def _resample(spectra: np.ndarray, source: np.ndarray, target: np.ndarray) -> np.ndarray:
    if len(source) == len(target) and np.allclose(source, target):
        return spectra.astype(np.float32, copy=False)
    return np.vstack([np.interp(target, source, row) for row in spectra]).astype(np.float32)


def _snv(spectra: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    means = spectra.mean(axis=1, keepdims=True)
    standard_deviations = spectra.std(axis=1, keepdims=True)
    valid = np.isfinite(standard_deviations[:, 0]) & (standard_deviations[:, 0] > 0)
    return ((spectra[valid] - means[valid]) / standard_deviations[valid]).astype(np.float32), valid


def _balanced_sample_indices(
    metadata: pd.DataFrame, *, maximum_rows: int, random_state: int
) -> np.ndarray:
    """Return an equal-size reproducible sample from every represented hole."""
    rng = np.random.default_rng(random_state)
    groups = metadata.groupby("hole_id", sort=True).indices
    rows_per_hole = max(1, maximum_rows // len(groups))
    selected = []
    for indices in groups.values():
        indices = np.asarray(indices)
        size = min(rows_per_hole, len(indices))
        selected.append(rng.choice(indices, size=size, replace=False))
    return np.sort(np.concatenate(selected))


def _cluster_stability(
    values: np.ndarray, *, k: int, repeats: int, random_state: int
) -> float:
    """Measure agreement between repeated K-Means fits using adjusted Rand index."""
    repeats = max(2, repeats)
    label_sets = [
        KMeans(n_clusters=k, random_state=random_state + repeat, n_init=10).fit_predict(values)
        for repeat in range(repeats)
    ]
    agreements = [
        adjusted_rand_score(label_sets[left], label_sets[right])
        for left in range(repeats)
        for right in range(left + 1, repeats)
    ]
    return float(np.mean(agreements))


def _rank_k_scores(
    scores: pd.DataFrame,
    *,
    minimum_cluster_percentage: float,
    minimum_hole_coverage: float,
) -> pd.DataFrame:
    """Combine complementary diagnostics without allowing one metric to dominate."""
    scores = scores.copy()
    if len(scores) > 2:
        k_span = scores["k"].max() - scores["k"].min()
        inertia_span = scores["inertia"].max() - scores["inertia"].min()
        x = (scores["k"] - scores["k"].min()) / k_span
        y = (scores["inertia"] - scores["inertia"].min()) / inertia_span
        line = 1 - x
        scores["elbow_strength"] = line - y
    else:
        scores["elbow_strength"] = 0.0

    ranks = pd.DataFrame(
        {
            "silhouette": scores["silhouette_score"].rank(pct=True),
            "calinski_harabasz": scores["calinski_harabasz_score"].rank(pct=True),
            "davies_bouldin": scores["davies_bouldin_score"].rank(
                ascending=False, pct=True
            ),
            "stability": scores["stability_ari"].rank(pct=True),
            "elbow": scores["elbow_strength"].rank(pct=True),
        }
    )
    scores["consensus_score"] = ranks.mean(axis=1)
    scores["passes_quality_checks"] = (
        (scores["minimum_cluster_percentage"] >= minimum_cluster_percentage)
        & (scores["minimum_hole_coverage"] >= minimum_hole_coverage)
    )
    scores.loc[~scores["passes_quality_checks"], "consensus_score"] = -1.0
    return scores


def _normalise_scalar_name(name: object) -> str:
    return "".join(character.lower() for character in str(name) if character.isalnum())

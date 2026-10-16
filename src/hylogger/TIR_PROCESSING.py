"""
YOU CAN COPY PASTE THIS ENTIRE CODE TO ANY LLM TO SUMMARIZE WHAT EACH STEP DO TO HELP BETTER UNDERSTANDING

TIR-focused pipeline for the 20-hole team cohort (team_raw_tsg_20).

Flow:
  0. Download the shared 20-hole cohort (run once)
  1. Check TIR data availability across the cohort
  2. Extract TIR spectra + scalar columns per hole
  3. Clean: remove duplicate rows, replace sentinel values, filter by Final Mask
  4. Decode numeric mineral/group codes into real names (some holes use codes, not text)
  5. Combine all holes, normalize (SNV), reduce dimensions (PCA)
  6. Cluster (K-means) and pick the best k
  7. Describe each cluster using TSG's own mineral labels (validation)
  8. Visualize: cluster shapes, composition by hole, depth spread
  9. Score hole-to-hole similarity based on cluster composition

Note: this is a working pipeline, not yet optimized or formally validated
(no stability check, no confound check against tray/hole_id yet).
"""

from __future__ import annotations

import subprocess
import re
import pandas as pd
import numpy as np

HOLE_IDS = [
    "KEDD002", "LD82625W1", "WKDD15001", "FDH002", "SPBD0364", "FDH007", "NBDH035",
    "MWEX10270", "22USDH0207", "15RCDVZ001", "16CADD002", "13TRDD012", "13TRDD013", "RRLGDDD093",
    "PND003", "PND005", "16MYD0047", "EPT2276", "16MYD0052", "PND004",
]

SELECTION_NAME = "team_raw_tsg_20"

DEFAULT_TIR_COLUMNS = (
    "Min1 ujCLST", "Min2 ujCLST", "Min3 ujCLST",
    "Grp1 ujCLST", "Grp2 ujCLST", "Grp3 ujCLST",
    "Wt1 ujCLST", "Wt2 ujCLST", "Wt3 ujCLST",
    "Error ujCLST", "NIL_Stat ujCLST", "SNR ujCLST",
    "Final Mask", "HyLogDiag",
)


# ---------------------------------------------------------------------------
# Step 0: Download the shared 20-hole cohort (run once)
# ---------------------------------------------------------------------------

def _run(command: list[str]) -> None:
    """Run a hylogger CLI command, streaming output live, and stop on failure."""
    print(f"\n$ {' '.join(command)}")
    result = subprocess.run(command)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(command)}")


def download_team_cohort() -> None:
    """Create the team selection and download raw + processed data for it."""
    check = subprocess.run(
        ["hylogger", "selection", "show", SELECTION_NAME],
        capture_output=True, text=True,
    )
    if check.returncode == 0:
        print(f"Selection '{SELECTION_NAME}' already exists, skipping creation.")
    else:
        _run([
            "hylogger", "selection", "create", SELECTION_NAME,
            "--holes", *HOLE_IDS,
            "--description", "Shared 20-hole raw and TSG team cohort",
        ])
    _run(["hylogger", "selection", "show", SELECTION_NAME])
    _run(["hylogger", "raw", "fetch", "--selection", SELECTION_NAME, "--components", "spectra", "--dry-run"])
    _run(["hylogger", "processed", "fetch", "--selection", SELECTION_NAME, "--profile", "minerals"])
    _run(["hylogger", "raw", "fetch", "--selection", SELECTION_NAME, "--components", "spectra"])
    _run(["hylogger", "status", "--selection", SELECTION_NAME])
    _run(["hylogger", "inspect", "PND004", "--report", "--open"])


# ---------------------------------------------------------------------------
# Step 1: Check TIR data availability across the 20-hole cohort
# ---------------------------------------------------------------------------

def check_tir_availability(hole_ids: list[str] | None = None) -> pd.DataFrame:
    """For the given hole_ids (defaults to all 20), count TIR vs SWIR logs."""
    hole_ids = hole_ids or HOLE_IDS
    rows = []
    for hole_id in hole_ids:
        result = subprocess.run(["hylogger", "processed", "preview", hole_id], capture_output=True, text=True)
        output = result.stdout
        total_match = re.search(r"(\d+)\s+displayed logs", output)
        total_logs = int(total_match.group(1)) if total_match else 0
        tir_lines = [line for line in output.splitlines() if re.search(r"\bTIR\b", line)]
        swir_lines = [line for line in output.splitlines() if re.search(r"\bSWIR\b", line)]
        rows.append({
            "hole_id": hole_id, "total_logs": total_logs,
            "tir_log_count": len(tir_lines), "swir_log_count": len(swir_lines),
            "has_tir": len(tir_lines) > 0,
        })
    summary = pd.DataFrame(rows)
    print(summary.to_string(index=False))
    print(f"\nHoles with TIR data: {summary['has_tir'].sum()} of {len(summary)}")
    return summary


def inspect_extra_logs(hole_id: str) -> None:
    """Print the full processed-log listing for a hole (diagnostic helper)."""
    result = subprocess.run(["hylogger", "processed", "preview", hole_id], capture_output=True, text=True)
    print(result.stdout)


# ---------------------------------------------------------------------------
# Step 2: Extract TIR spectra + scalars for one hole, and for all 20
# ---------------------------------------------------------------------------

def load_hole_tir(hole_id: str) -> dict:
    """Load TIR spectra + standard ujCLST scalar columns for one downloaded hole."""
    from hylogger.config import AppConfig
    from hylogger.raw import find_package_dir
    from pytsg import parse_tsg

    hole_id = hole_id.upper()
    config = AppConfig.load()
    package_dir = find_package_dir(config, hole_id)
    if package_dir is None:
        raise RuntimeError(f"No extracted raw package for {hole_id}.")

    package = parse_tsg.read_package(package_dir, read_cras_file=False)
    tir = package.tir

    spectra = tir.spectra
    wavelengths = tir.wavelength
    scalars = tir.scalars.reset_index(drop=True)
    headers = tir.sampleheaders.reset_index(drop=True)

    metadata = pd.DataFrame({
        "hole_id": hole_id,
        "depth_m": pd.to_numeric(headers["D"], errors="coerce") if "D" in headers.columns else scalars["Depth (m)"],
        "tray": headers["T"] if "T" in headers.columns else scalars["Tray"],
    })
    for col in DEFAULT_TIR_COLUMNS:
        if col in scalars.columns:
            metadata[col] = scalars[col].to_numpy()

    return {
        "hole_id": hole_id,
        "wavelengths": wavelengths,
        "spectra": spectra,
        "metadata": metadata,
        "classes": tir.classes,
    }


def load_all_tir_holes(hole_ids: list[str] | None = None) -> dict:
    """
    Load TIR data for the given hole_ids (defaults to the full HOLE_IDS
    cohort if not specified). Pass a subset to work with fewer holes.
    """
    hole_ids = hole_ids or HOLE_IDS
    holes = {}
    for hole_id in hole_ids:
        try:
            holes[hole_id] = load_hole_tir(hole_id)
            print(f"{hole_id}: loaded {len(holes[hole_id]['metadata'])} rows")
        except Exception as error:
            print(f"{hole_id}: FAILED to load — {error}")
    return holes


# ---------------------------------------------------------------------------
# Step 3: Clean — duplicates, sentinel values, Final Mask filtering
# ---------------------------------------------------------------------------

def remove_duplicate_depths(holes: dict) -> dict:
    """Drop exact-duplicate rows (all columns) per hole, keeping the first occurrence."""
    cleaned = {}
    for hole_id, hole in holes.items():
        metadata = hole["metadata"]
        total_before = len(metadata)
        unique_mask = ~metadata.duplicated(keep="first")
        new_metadata = metadata.loc[unique_mask].reset_index(drop=True)
        new_spectra = hole["spectra"][unique_mask.to_numpy()]
        cleaned[hole_id] = {**hole, "spectra": new_spectra, "metadata": new_metadata}
        print(f"{hole_id}: {total_before} -> {len(new_metadata)} rows ({total_before - len(new_metadata)} exact duplicates dropped)")
    return cleaned


def clean_tir_holes(holes: dict) -> dict:
    """Replace TSG's sentinel (< -1e38) with NaN, then keep only Final Mask == 1 + finite spectra rows."""
    cleaned = {}
    for hole_id, hole in holes.items():
        metadata = hole["metadata"].copy()
        spectra = hole["spectra"]
        total_before = len(metadata)

        for col in metadata.columns:
            if pd.api.types.is_numeric_dtype(metadata[col]):
                metadata[col] = metadata[col].where(metadata[col] > -1e38)

        keep = np.isfinite(spectra).all(axis=1) & (metadata["Final Mask"].to_numpy() == 1)
        new_metadata = metadata.loc[keep].reset_index(drop=True)
        new_spectra = spectra[keep]

        print(f"{hole_id}: {total_before} -> {len(new_metadata)} rows ({total_before - len(new_metadata)} masked/invalid dropped)")
        cleaned[hole_id] = {**hole, "spectra": new_spectra, "metadata": new_metadata}
    return cleaned


# ---------------------------------------------------------------------------
# Step 4: Decode numeric mineral/group/diagnostic codes (some holes use
# codes instead of text — decoded via each hole's own package.tir.classes)
# ---------------------------------------------------------------------------

def decode_tir_codes(holes: dict) -> dict:
    """Decode numeric Min/Grp/HyLogDiag codes into text, using each hole's own lookup table."""
    decoded = {}
    for hole_id, hole in holes.items():
        metadata = hole["metadata"].copy()
        classes = hole.get("classes", {})

        mineral_lookup = next((c.classes for c in classes.values() if "Minerals" in c.name and c.name.startswith("U_")), None)
        group_lookup = next((c.classes for c in classes.values() if "Groups" in c.name and c.name.startswith("U_")), None)
        diag_lookup = next((c.classes for c in classes.values() if c.name == "HyLogDiag"), None)

        def decode_column(series, lookup):
            if lookup is None:
                return series
            def _decode(value):
                try:
                    code = int(float(value))
                except (ValueError, TypeError):
                    return value
                if code < 0 or code > 10_000:  # rejects the TSG sentinel and other garbage
                    return value
                return lookup.get(code, value)
            return series.apply(_decode)

        for col in ["Min1 ujCLST", "Min2 ujCLST", "Min3 ujCLST"]:
            if col in metadata.columns:
                metadata[col] = decode_column(metadata[col], mineral_lookup)
        for col in ["Grp1 ujCLST", "Grp2 ujCLST", "Grp3 ujCLST"]:
            if col in metadata.columns:
                metadata[col] = decode_column(metadata[col], group_lookup)
        if "HyLogDiag" in metadata.columns:
            metadata["HyLogDiag"] = decode_column(metadata["HyLogDiag"], diag_lookup)

        decoded[hole_id] = {**hole, "metadata": metadata}
        n_changed = (decoded[hole_id]["metadata"]["Min1 ujCLST"] != hole["metadata"]["Min1 ujCLST"]).sum()
        if n_changed:
            print(f"{hole_id}: decoded {n_changed} numeric mineral codes")
    return decoded


# ---------------------------------------------------------------------------
# Step 5: Combine holes, normalize (SNV), and run PCA
# ---------------------------------------------------------------------------

def _common_wavelengths_tir(holes: dict) -> np.ndarray:
    """Find the wavelength range every hole has in common."""
    all_wavelengths = [hole["wavelengths"] for hole in holes.values()]
    lower = max(float(w.min()) for w in all_wavelengths)
    upper = min(float(w.max()) for w in all_wavelengths)
    reference = all_wavelengths[0]
    common = reference[(reference >= lower) & (reference <= upper)]
    if len(common) < 2:
        raise RuntimeError("No common TIR wavelength range across holes.")
    return common


def _resample_tir(spectra: np.ndarray, source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Interpolate spectra onto a shared wavelength grid (no-op if already matching)."""
    if len(source) == len(target) and np.allclose(source, target):
        return spectra.astype(np.float32, copy=False)
    return np.vstack([np.interp(target, source, row) for row in spectra]).astype(np.float32)


def _snv_tir(spectra: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Standard Normal Variate: per-spectrum (mean=0, std=1)."""
    means = spectra.mean(axis=1, keepdims=True)
    stds = spectra.std(axis=1, keepdims=True)
    valid = np.isfinite(stds[:, 0]) & (stds[:, 0] > 0)
    return ((spectra[valid] - means[valid]) / stds[valid]).astype(np.float32), valid


def run_tir_pca(holes: dict, *, variance_retained: float = 0.95) -> dict:
    """Combine all holes' TIR spectra onto a common wavelength grid, apply SNV, then PCA."""
    from sklearn.decomposition import PCA

    wavelengths = _common_wavelengths_tir(holes)
    spectra_parts = [_resample_tir(hole["spectra"], hole["wavelengths"], wavelengths) for hole in holes.values()]
    metadata_parts = [hole["metadata"] for hole in holes.values()]

    spectra = np.vstack(spectra_parts)
    metadata = pd.concat(metadata_parts, ignore_index=True)

    snv, valid = _snv_tir(spectra)
    metadata = metadata.loc[valid].reset_index(drop=True)

    pca = PCA(n_components=variance_retained, svd_solver="full")
    pca.fit(snv)
    spectra_pca = pca.transform(snv)

    print(f"Combined spectra shape (after SNV): {snv.shape}")
    print(f"PCA output shape: {spectra_pca.shape}")
    print(f"Components kept: {pca.n_components_}, variance explained: {pca.explained_variance_ratio_.sum():.4f}")

    return {"wavelengths": wavelengths, "metadata": metadata, "snv": snv, "spectra_pca": spectra_pca, "pca": pca}


# ---------------------------------------------------------------------------
# Step 6: K-means clustering on the PCA output
# ---------------------------------------------------------------------------

def cluster_tir_pca(pca_result: dict, *, n_clusters=None, k_values=range(2, 9), random_state=42) -> dict:
    """Run K-means across candidate k values on PCA output; pick best by silhouette score."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    spectra_pca, metadata, snv = pca_result["spectra_pca"], pca_result["metadata"], pca_result["snv"]
    requested_k = [n_clusters] if n_clusters else list(k_values)

    candidates = {}
    for k in requested_k:
        model = KMeans(n_clusters=k, random_state=random_state, n_init=20).fit(spectra_pca)
        score = silhouette_score(spectra_pca, model.labels_, sample_size=10_000, random_state=random_state)
        candidates[k] = (model, score)
        print(f"k={k}: silhouette={score:.4f}, inertia={model.inertia_:.1f}")

    k_scores = pd.DataFrame([{"k": k, "silhouette_score": s, "inertia": m.inertia_} for k, (m, s) in candidates.items()])
    selected_k = n_clusters or k_scores.loc[k_scores["silhouette_score"].idxmax(), "k"]
    k_scores["selected"] = k_scores["k"].eq(selected_k)
    model = candidates[selected_k][0]

    assignments = metadata.copy()
    assignments["cluster_id"] = model.labels_
    cluster_sizes = assignments.groupby(["hole_id", "cluster_id"], as_index=False).size().rename(columns={"size": "spectra_count"})
    cluster_sizes["percentage"] = (cluster_sizes["spectra_count"] / cluster_sizes.groupby("hole_id")["spectra_count"].transform("sum") * 100).round(2)
    mean_spectra = np.vstack([snv[model.labels_ == c].mean(axis=0) for c in range(selected_k)])

    print(f"\nSelected k: {selected_k}")
    return {
        "assignments": assignments, "cluster_sizes": cluster_sizes, "model": model,
        "pca": pca_result["pca"], "wavelengths": pca_result["wavelengths"],
        "mean_snv_spectra": mean_spectra, "k_scores": k_scores,
        "spectra_pca": spectra_pca,
    }


def plot_elbow_and_silhouette(result: dict) -> None:
    """Plot inertia (elbow) and silhouette score side by side, across k."""
    import matplotlib.pyplot as plt

    k_scores = result["k_scores"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].plot(k_scores["k"], k_scores["inertia"], marker="o")
    axes[0].set(xlabel="k", ylabel="Inertia", title="Elbow method")
    axes[1].plot(k_scores["k"], k_scores["silhouette_score"], marker="o", color="orange")
    axes[1].set(xlabel="k", ylabel="Silhouette score", title="Silhouette score by k")
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# Step 7: Describe each cluster using TSG's own mineral labels (validation)
# ---------------------------------------------------------------------------

def describe_tir_clusters(result: dict, top_n: int = 5) -> None:
    """For each cluster, show the most common TSG mineral labels among its readings."""
    assignments = result["assignments"]
    for cluster_id in sorted(assignments["cluster_id"].unique()):
        subset = assignments[assignments["cluster_id"] == cluster_id]
        labeled = subset[subset["Min1 ujCLST"].notna() & (subset["Min1 ujCLST"] != "")]

        print(f"=== Cluster {cluster_id} ({len(subset):,} readings, {len(labeled):,} with a TSG label) ===")
        if len(labeled) > 0:
            print("Top minerals (Min1 ujCLST):")
            print(labeled["Min1 ujCLST"].value_counts().head(top_n))
            print("\nTop mineral groups (Grp1 ujCLST):")
            print(labeled["Grp1 ujCLST"].value_counts().head(top_n))
        else:
            print("No TSG-labeled readings in this cluster.")
        print()


# ---------------------------------------------------------------------------
# Step 8: Visualize — cluster shapes, composition, depth spread
# ---------------------------------------------------------------------------

def plot_tir_dashboard(result: dict) -> None:
    """4-panel overview: mean spectra, overall sizes, hole composition heatmap, depth spread."""
    import matplotlib.pyplot as plt

    assignments = result["assignments"]
    n_clusters = len(result["mean_snv_spectra"])
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    ax = axes[0, 0]
    for c in range(n_clusters):
        ax.plot(result["wavelengths"], result["mean_snv_spectra"][c], label=f"Cluster {c}")
    ax.set(xlabel="Wavelength (nm)", ylabel="SNV reflectance", title="Mean spectrum per cluster")
    ax.legend()

    ax = axes[0, 1]
    overall = assignments["cluster_id"].value_counts().sort_index()
    ax.bar(overall.index.astype(str), overall.values)
    for i, v in enumerate(overall.values):
        ax.text(i, v, f"{v/overall.sum()*100:.1f}%", ha="center", va="bottom")
    ax.set(xlabel="Cluster ID", ylabel="Readings", title="Overall cluster sizes")

    ax = axes[1, 0]
    pivot = result["cluster_sizes"].pivot(index="hole_id", columns="cluster_id", values="percentage").fillna(0)
    im = ax.imshow(pivot.values, aspect="auto", cmap="viridis")
    ax.set_yticks(range(len(pivot.index))); ax.set_yticklabels(pivot.index, fontsize=7)
    ax.set_xticks(range(n_clusters)); ax.set_xticklabels([f"Cluster {c}" for c in pivot.columns])
    ax.set_title("Cluster composition by hole (%)")
    plt.colorbar(im, ax=ax, label="% of hole's readings")

    ax = axes[1, 1]
    data = [assignments.loc[assignments["cluster_id"] == c, "depth_m"] for c in range(n_clusters)]
    ax.boxplot(data, tick_labels=[f"Cluster {c}" for c in range(n_clusters)])
    ax.set(ylabel="Depth (m)", title="Depth distribution per cluster (all holes)")

    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# Step 9: Hole-to-hole similarity, based on cluster composition
# ---------------------------------------------------------------------------

def hole_similarity_matrix(result: dict) -> pd.DataFrame:
    """Cosine similarity between every pair of holes, based on % readings per cluster."""
    from sklearn.metrics.pairwise import cosine_similarity

    pivot = result["cluster_sizes"].pivot(index="hole_id", columns="cluster_id", values="percentage").fillna(0.0)
    scores = cosine_similarity(pivot.values)
    return pd.DataFrame(scores, index=pivot.index, columns=pivot.index)


def plot_hole_similarity_heatmap(similarity_df: pd.DataFrame) -> None:
    """Plot a hole-by-hole similarity heatmap, annotated with percentages."""
    import matplotlib.pyplot as plt

    n = len(similarity_df)
    fig, ax = plt.subplots(figsize=(max(14, n * 0.7), max(12, n * 0.6)))
    im = ax.imshow(similarity_df.values, cmap="viridis", vmin=0, vmax=1)

    ax.set_xticks(range(len(similarity_df.columns)))
    ax.set_xticklabels(similarity_df.columns, rotation=90, fontsize=9)
    ax.set_yticks(range(len(similarity_df.index)))
    ax.set_yticklabels(similarity_df.index, fontsize=9)

    for i in range(len(similarity_df.index)):
        for j in range(len(similarity_df.columns)):
            value = similarity_df.values[i, j]
            text_color = "black" if value > 0.55 else "white"
            ax.text(j, i, f"{value*100:.0f}%", ha="center", va="center", color=text_color, fontsize=7)

    ax.set_title("Hole-to-hole similarity (based on TIR cluster composition)", fontsize=14, pad=15)
    plt.colorbar(im, ax=ax, label="Cosine similarity (%)")
    plt.tight_layout()
    plt.show()

# ---------------------------------------------------------------------------
# Step 9b: Whole-hole baseline clustering (Grp1 ujCLST) — for comparison
# against the per-reading spectral clusters above, not as validation.
# ---------------------------------------------------------------------------

def run_whole_hole_baseline(config, selection=SELECTION_NAME, feature_log="grp1_uj_clst",
                             n_clusters=2, random_state=42):
    """
    Cluster whole holes using TSG's own Grp1 ujCLST mineral-group proportions,
    independent of the raw-spectral pipeline above. Mirrors the team's
    02_tir_ujclst_clustering.ipynb baseline exactly (no PCA in the clustering
    itself; PCA is only used later for visualization).
    """
    from hylogger.baseline import fit_baseline, load_baseline_inputs

    inputs = load_baseline_inputs(config, selection, feature_log=feature_log)
    result = fit_baseline(
        inputs,
        n_clusters=n_clusters,
        random_state=random_state,
        pca_variance=None,
    )

    print(f"Whole-hole baseline ({feature_log}): silhouette={result.metrics['silhouette_score']:.3f}, "
          f"cluster sizes={result.metrics['cluster_sizes']}")
    if result.metrics["has_singleton_cluster"]:
        print("Caution: at least one whole-hole cluster contains only one hole.")

    return inputs, result

def check_baseline_stability(inputs, n_clusters=2, n_seeds=10):
    """
    Run K-means on the whole-hole proportions with different random seeds and
    compute pairwise Adjusted Rand Index (ARI) between the resulting label
    sets. Low mean/min ARI means the clustering is sensitive to initialization.
    """
    from itertools import combinations
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score
    from sklearn.preprocessing import StandardScaler

    varying = inputs.proportions.columns[inputs.proportions.nunique() > 1].tolist()
    X_scaled = StandardScaler().fit_transform(inputs.proportions[varying])

    label_sets = {}
    for seed in range(n_seeds):
        model = KMeans(n_clusters=n_clusters, random_state=seed, n_init=20)
        label_sets[seed] = model.fit_predict(X_scaled)

    ari_scores = [
        adjusted_rand_score(label_sets[a], label_sets[b])
        for a, b in combinations(label_sets.keys(), 2)
    ]

    print(f"Stability check ({n_seeds} seeds, {len(ari_scores)} pairs): "
          f"mean ARI={sum(ari_scores)/len(ari_scores):.3f}, "
          f"min ARI={min(ari_scores):.3f}, max ARI={max(ari_scores):.3f}")

    return {"mean_ari": sum(ari_scores) / len(ari_scores), "min_ari": min(ari_scores), "max_ari": max(ari_scores)}


def check_baseline_scaling_sensitivity(inputs, n_clusters=2, random_state=42):
    """
    Compare the whole-hole clustering with vs. without StandardScaler.
    A low ARI here means the cluster split is an artifact of scaling choice,
    not a robust pattern in the raw proportions.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score
    from sklearn.preprocessing import StandardScaler

    varying = inputs.proportions.columns[inputs.proportions.nunique() > 1].tolist()

    X_scaled = StandardScaler().fit_transform(inputs.proportions[varying])
    scaled_labels = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=20).fit_predict(X_scaled)

    X_unscaled = inputs.proportions[varying]
    unscaled_labels = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=20).fit_predict(X_unscaled)

    ari = adjusted_rand_score(scaled_labels, unscaled_labels)
    print(f"Scaled-vs-unscaled sensitivity: ARI={ari:.3f} "
          f"({'robust' if ari > 0.5 else 'fragile — depends heavily on scaling'})")

    return ari


# ---------------------------------------------------------------------------
# Step 10: Interactive multi-panel dashboard (3D PCA + supporting 2D views)
# ---------------------------------------------------------------------------

def plot_tir_dashboard_3d(result: dict, *, sample_size: int = 8_000, random_state: int = 42) -> None:
    """
    Interactive multi-panel dashboard: 3D PCA scatter (top-left), mean
    spectrum per cluster (top-right), cluster composition by hole heatmap
    (bottom-left), depth distribution per cluster (bottom-right).
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    spectra_pca = result["spectra_pca"]
    assignments = result["assignments"]
    n_clusters = len(result["mean_snv_spectra"])
    colors = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A", "#19D3F3", "#FF6692", "#B6E880"]

    n = len(assignments)
    if n > sample_size:
        rng = np.random.default_rng(random_state)
        idx = rng.choice(n, size=sample_size, replace=False)
    else:
        idx = np.arange(n)

    fig = make_subplots(
        rows=2, cols=2,
        specs=[[{"type": "scene"}, {"type": "xy"}], [{"type": "xy"}, {"type": "xy"}]],
        subplot_titles=(
            "PCA space (3D, colored by cluster)", "Mean spectrum per cluster",
            "Cluster composition by hole (%)", "Depth distribution per cluster",
        ),
    )

    for c in range(n_clusters):
        mask = assignments["cluster_id"].to_numpy()[idx] == c
        fig.add_trace(
            go.Scatter3d(
                x=spectra_pca[idx][mask, 0], y=spectra_pca[idx][mask, 1], z=spectra_pca[idx][mask, 2],
                mode="markers", marker=dict(size=2, color=colors[c % len(colors)], opacity=0.5),
                name=f"Cluster {c}", legendgroup=f"Cluster {c}",
            ),
            row=1, col=1,
        )

    for c in range(n_clusters):
        fig.add_trace(
            go.Scatter(
                x=result["wavelengths"], y=result["mean_snv_spectra"][c],
                mode="lines", line=dict(color=colors[c % len(colors)]),
                name=f"Cluster {c}", legendgroup=f"Cluster {c}", showlegend=False,
            ),
            row=1, col=2,
        )

    pivot = result["cluster_sizes"].pivot(index="hole_id", columns="cluster_id", values="percentage").fillna(0)
    fig.add_trace(
        go.Heatmap(
            z=pivot.values, x=[f"Cluster {c}" for c in pivot.columns], y=pivot.index,
            colorscale="Viridis", colorbar=dict(title="%", x=0.44, y=0.22, len=0.4),
        ),
        row=2, col=1,
    )

    for c in range(n_clusters):
        fig.add_trace(
            go.Box(
                y=assignments.loc[assignments["cluster_id"] == c, "depth_m"],
                name=f"Cluster {c}", marker_color=colors[c % len(colors)],
                legendgroup=f"Cluster {c}", showlegend=False,
            ),
            row=2, col=2,
        )

    fig.update_layout(height=900, width=1200, title_text="TIR clustering — interactive dashboard")
    fig.show()


# ---------------------------------------------------------------------------
# Step 11: Dummy dashboard example
# ---------------------------------------------------------------------------
def _json_default(o):
    import numpy as np
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"Object of type {type(o)} is not JSON serializable")

def export_tir_client_dashboard(
    holes, result, similarity_df,
    cluster_labels=None,
    whole_hole_inputs=None,
    whole_hole_result=None,
    output_path="tir_client_dashboard.html",
    open_browser=True,
):
    """
    Step 11: writes a styled, standalone HTML dashboard (hole selector + summary
    text + similarity + real spectra + depth profile + top named minerals) using
    real numbers from your pipeline. Plain HTML/CSS/JS, not a Plotly figure.
    Open the output file in any browser; no server or extra packages needed.
    """
    import json
    import webbrowser
    import numpy as np

    assignments = result["assignments"]
    hole_ids = sorted(assignments["hole_id"].unique())
    n_clusters = assignments["cluster_id"].nunique()

    if cluster_labels is None:
        cluster_labels = {i: f"Cluster {i}" for i in range(n_clusters)}
    cluster_names = [cluster_labels[i] for i in range(n_clusters)]
    palette = ["#c9a53b", "#5b8c78", "#5a5f7a", "#8a5a34", "#b23a48", "#2f6f8f"]
    cluster_cols = [palette[i % len(palette)] for i in range(n_clusters)]

    counts = assignments.groupby(["hole_id", "cluster_id"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=range(n_clusters), fill_value=0)
    comp = counts.div(counts.sum(axis=1), axis=0) * 100
    comp = comp.reindex(hole_ids)

    N_DEPTH_BINS = 30
    holes_data = {}

    #Optional whole-hole comparison data (from run_whole_hole_baseline)
    wh_proportions = wh_assignments = wh_quality = None
    if whole_hole_inputs is not None and whole_hole_result is not None:
        wh_proportions = whole_hole_inputs.proportions
        wh_quality = whole_hole_inputs.quality
        wh_assignments = whole_hole_result.assignments.set_index("hole_id")["cluster_id"]

    for hole in hole_ids:
        vals = comp.loc[hole].values.tolist()
        dominant = int(np.argmax(vals))

        meta = holes[hole]["metadata"]
        labeled = meta[meta["Min1 ujCLST"].astype(str).str.strip() != ""]
        mineral_counts = labeled["Min1 ujCLST"].value_counts()
        n_minerals = int(mineral_counts.shape[0])
        top6 = mineral_counts.head(6)
        top6_pct = (top6 / len(labeled) * 100) if len(labeled) else top6 * 0

        sims = similarity_df.loc[hole].drop(hole).sort_values(ascending=False).head(5)

        # Depth profile: bin this hole's cleaned points by depth, get cluster % per bin
        sub = assignments[assignments["hole_id"] == hole]
        depths_arr = sub["depth_m"].to_numpy()
        clusters_arr = sub["cluster_id"].to_numpy()
        edges = np.linspace(depths_arr.min(), depths_arr.max(), N_DEPTH_BINS + 1)
        bin_idx = np.clip(np.digitize(depths_arr, edges) - 1, 0, N_DEPTH_BINS - 1)
        depth_bins = []
        for b in range(N_DEPTH_BINS):
            mask = bin_idx == b
            if mask.sum() == 0:
                depth_bins.append(None)
            else:
                c = np.bincount(clusters_arr[mask], minlength=n_clusters)
                depth_bins.append([float(x) for x in (c / c.sum() * 100)])

        if wh_proportions is not None and hole in wh_proportions.index:
            grp1_row = (wh_proportions.loc[hole] * 100).sort_values(ascending=False)
            grp1_composition = [[str(k), float(v)] for k, v in grp1_row.items() if v > 0]
            whole_hole_cluster = int(wh_assignments.loc[hole]) if hole in wh_assignments.index else None
            qc_valid_pct = float(wh_quality.loc[hole, "valid_pct"]) if hole in wh_quality.index else None
        else:
            grp1_composition, whole_hole_cluster, qc_valid_pct = [], None, None
        
        holes_data[hole] = {
            "composition": vals,
            "dominant": dominant,
            "depth_min": float(meta["depth_m"].min()),
            "depth_max": float(meta["depth_m"].max()),
            "n_points": int(len(meta)),
            "n_minerals": n_minerals,
            "top_mineral": mineral_counts.index[0] if n_minerals else "n/a",
            "top_mineral_pct": float(mineral_counts.iloc[0] / len(labeled) * 100) if n_minerals else 0.0,
            "top_minerals": [[str(k), float(v)] for k, v in zip(top6_pct.index, top6_pct.values)],
            "similar": [[str(k), float(v) * 100] for k, v in sims.items()],
            "grp1_composition": grp1_composition,
            "whole_hole_cluster": whole_hole_cluster,
            "qc_valid_pct": qc_valid_pct,
            "depth_profile": {
                "depth_min": float(depths_arr.min()),
                "depth_max": float(depths_arr.max()),
                "bins": depth_bins,
            },
        }

    wavelengths = [float(w) for w in result["wavelengths"]]
    mean_spectra = [[float(v) for v in result["mean_snv_spectra"][c]] for c in range(n_clusters)]

    # k-selection: silhouette score per candidate k, to justify choosing k=3
    k_scores = result.get("k_scores")
    k_list, sil_list = [], []
    if isinstance(k_scores, dict):
        for k, v in sorted(k_scores.items()):
            k_list.append(int(k))
            sil_list.append(float(v["silhouette"]) if isinstance(v, dict) else float(v))
    elif hasattr(k_scores, "iterrows"):
        for _, row in k_scores.sort_values("k").iterrows():
            k_list.append(int(row["k"]))
            sil_list.append(float(row["silhouette_score"]))

    # cluster composition breakdown: top named minerals inside each cluster, cohort-wide
    labeled_all = assignments[assignments["Min1 ujCLST"].astype(str).str.strip() != ""]
    cluster_breakdown = []
    for c in range(n_clusters):
        sub = labeled_all[labeled_all["cluster_id"] == c]
        vc = sub["Min1 ujCLST"].value_counts()
        top5 = vc.head(5)
        total = vc.sum()
        cluster_breakdown.append({
            "cluster": cluster_names[c],
            "color": cluster_cols[c],
            "minerals": [[str(k), float(v / total * 100)] for k, v in top5.items()] if total else [],
        })

    payload = {
        "holeIds": hole_ids,
        "clusterNames": cluster_names,
        "clusterCols": cluster_cols,
        "holes": holes_data,
        "wavelengths": wavelengths,
        "meanSpectra": mean_spectra,
        "kSelection": {"k": k_list, "silhouette": sil_list, "chosenK": n_clusters},
        "clusterBreakdown": cluster_breakdown,
    }

    html = _TIR_DASHBOARD_TEMPLATE.replace("__DATA_JSON__", json.dumps(payload, default=_json_default))
    with open(output_path, "w") as f:
        f.write(html)

    print(f"Dashboard written to {output_path}")
    if open_browser:
        webbrowser.open(f"file://{__import__('os').path.abspath(output_path)}")


_TIR_DASHBOARD_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>TIR Hole Explorer</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{
  --bg:#f4f1ec; --surface:#ffffff; --ink:#221d17; --ink-dim:#6b6152; --line:#ddd4c3; --accent:#8a5a34;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font-family:'IBM Plex Sans',system-ui,sans-serif;padding:12px 16px;min-height:100vh}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
.wrap{max-width:1280px;margin:0 auto;height:calc(100vh - 24px);min-height:600px;display:flex;flex-direction:column;gap:10px}
header{flex:none;display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px}
header .titleblock{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
header h1{font-size:19px;font-weight:600;margin:0}
header .eyebrow{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
header p{color:var(--ink-dim);font-size:11.5px;margin:0}
.layout{flex:1;min-height:0;display:grid;grid-template-columns:190px minmax(0,1fr);gap:10px}
.rail{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px;overflow-y:auto;min-height:0}
.rail-label{font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-dim);padding:4px 6px 6px}
.hole-btn{display:flex;justify-content:space-between;align-items:center;gap:6px;width:100%;text-align:left;border:1px solid transparent;background:none;color:var(--ink);font:inherit;font-size:12px;padding:6px 7px;border-radius:6px;cursor:pointer}
.hole-btn:hover{background:var(--bg)}
.hole-btn[aria-pressed="true"]{background:var(--accent);color:#fff}
.hole-btn .dot{width:7px;height:7px;border-radius:50%;flex:none}
.hole-btn .pct{font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--ink-dim)}
.hole-btn[aria-pressed="true"] .pct{color:rgba(255,255,255,.85)}
.main{min-height:0;display:flex;flex-direction:column;gap:10px;overflow-y:auto}
.main > *{flex:none}
.grid2b{display:grid;grid-template-columns:1fr 1fr;gap:10px;min-height:0;align-items:stretch}
.grid2b > div {
    overflow: hidden;
}
.grid2c{display:grid;grid-template-columns:1fr 1fr;gap:10px;min-height:0;align-items:start}
.grid2c > div { overflow: hidden; }
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0;display:flex;flex-direction:column;min-height:0}
.card h2{font-size:11.5px;letter-spacing:.03em;text-transform:uppercase;color:var(--ink-dim);font-weight:600;margin:0;flex:none}
.card .sub{font-size:10.5px;color:var(--ink-dim);margin-top:1px;line-height:1.4;flex:none}
.topcard{flex-direction:row;flex-wrap:wrap;align-items:center;gap:18px;padding:10px 14px}
.hole-head{display:flex;align-items:baseline;gap:8px;flex:none}
.hole-head h1{font-size:18px;margin:0}
.hole-head .depth{font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--ink-dim)}
.summary{font-size:11.5px;line-height:1.5;flex:1 1 320px;min-width:260px}
.summary b{color:var(--accent)}
.bars{display:flex;flex-direction:column;gap:5px;flex:0 0 260px}
.bar-row{display:grid;grid-template-columns:88px minmax(0,1fr) 44px;gap:6px;align-items:center}
.bar-row .name{font-size:11px;display:flex;align-items:center;gap:5px}
.bar-row .name .dot{width:7px;height:7px;border-radius:50%;flex:none}
.bar-track{background:var(--bg);border-radius:5px;height:9px;overflow:hidden;border:1px solid var(--line)}
.bar-fill{height:100%;border-radius:5px 0 0 5px}
.bar-row .val{font-family:'IBM Plex Mono',monospace;font-size:11px;text-align:right}
.flag{display:flex;gap:6px;align-items:flex-start;background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:6px 9px;font-size:10.5px;color:var(--ink-dim);line-height:1.4;flex:0 0 230px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;min-height:0;align-items:stretch}
.chartbox{flex:1;min-height:0;position:relative;margin-top:6px}
.chartbox svg{width:100%;height:100%;display:block}
.simlist{display:flex;flex-direction:column;gap:3px;margin-top:6px;overflow-y:auto;min-height:0}
.simrow{display:grid;grid-template-columns:minmax(0,1fr) auto 42px;gap:6px;align-items:center;font-size:11px;padding:4px 0;border-bottom:1px solid var(--line)}
.simrow:last-child{border-bottom:none}
.simrow .simtrack{background:var(--bg);border-radius:5px;height:7px;overflow:hidden;border:1px solid var(--line)}
.simrow .simfill{height:100%;background:var(--accent);border-radius:5px}
.simrow .simval{font-family:'IBM Plex Mono',monospace;text-align:right;color:var(--ink-dim);font-size:10.5px}
.minlist{display:flex;flex-direction:column;gap:5px;margin-top:6px;overflow-y:auto;min-height:0}
#grp1List{max-height:240px}
.minrow{display:grid;grid-template-columns:82px minmax(0,1fr) 38px;gap:6px;align-items:center;font-size:10.5px}
.minrow .mintrack{background:var(--bg);border-radius:5px;height:9px;overflow:hidden;border:1px solid var(--line)}
.minrow .minfill{height:100%;border-radius:5px}
svg text{fill:var(--ink-dim);font-family:'IBM Plex Mono',monospace;font-size:9px}
</style></head>
<body><div class="wrap">
<header>
<div class="titleblock">
<span class="eyebrow">GSWA HyLogger &middot; TIR</span>
<h1>TIR Hole Explorer</h1>
</div>
<p>Pick a hole to see composition, similarity, spectra and depth profile at a glance.</p>
</header>
<div class="layout">
<nav class="rail" id="rail"><div class="rail-label" id="railCount"></div><div id="holeList"></div></nav>
<div class="main">
<div class="card topcard">
<div class="hole-head"><h1 id="holeName" class="mono">&mdash;</h1><span class="depth" id="holeMeta"></span></div>
<div class="summary" id="summaryText"></div>
<div class="bars" id="bars"></div>
<div class="flag"><span>&#9888;</span><span>TIR cannot detect gold. Groups shown are the pipeline's 3 TIR clusters: Silica/Quartz, Carbonate, Mafic/ultramafic.</span></div>
</div>
<div class="grid4">
<div class="card">
<h2>Most similar holes</h2>
<div class="sub">Full-spectra cosine similarity.</div>
<div class="simlist" id="simlist"></div>
</div>
<div class="card">
<h2>Cluster mean spectra</h2>
<div class="sub">Real mean SNV spectra per cluster.</div>
<div class="chartbox"><svg id="waveChart" viewBox="0 0 260 220" preserveAspectRatio="xMidYMid meet"></svg></div>
</div>
<div class="card">
<h2>Depth profile</h2>
<div class="sub">Composition by depth, shallow&rarr;deep.</div>
<div class="chartbox"><svg id="depthChart" viewBox="0 0 240 240" preserveAspectRatio="xMidYMid meet"></svg></div>
</div>
<div class="card">
<h2>Top named minerals</h2>
<div class="sub">From Min1 ujCLST, real counts.</div>
<div class="minlist" id="minlist"></div>
</div>
</div>
<div class="grid2b">
<div class="card">
<h2>Choosing k=3</h2>
<div class="sub">Silhouette score across candidate k values.</div>
<div class="chartbox" style="flex:none;height:140px"><svg id="kChart" viewBox="0 0 260 140" width="260" height="140" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%;display:block"></svg></div>
</div>
<div class="card">
<h2>What's inside each cluster</h2>
<div class="sub">Top named minerals per cluster, cohort-wide.</div>
<div id="clusterBreakdown" style="display:flex;gap:14px;margin-top:6px"></div>
</div>
</div>
<div class="grid2c">
<div class="card">
<h2>Whole-hole mineral profile (Grp1 ujCLST)</h2>
<div class="sub">TSG's own mineral-group interpretation, % of valid readings for this hole.</div>
<div class="minlist" id="grp1List"></div>
</div>
<div class="card">
<h2>Whole-hole clustering</h2>
<div class="sub">Independent clustering on Grp1 ujCLST proportions (k=2 baseline). Shown for comparison only — not a validation of the spectral clusters above.</div>
<div id="wholeHoleInfo" style="font-size:11.5px;line-height:1.6;margin-top:6px"></div>
</div>
</div>
</div>
</div>
</div>
<script>
function drawKChart(){
  const svg=document.getElementById('kChart');
  const ks=DATA.kSelection.k, sils=DATA.kSelection.silhouette, chosen=DATA.kSelection.chosenK;
  if(!ks.length){svg.innerHTML='<text x="10" y="20">k-selection data not available</text>';return;}
  const W=260,H=140,padL=30,padR=10,padT=10,padB=22;
  const plotW=W-padL-padR, plotH=H-padT-padB;
  const kmin=Math.min(...ks), kmax=Math.max(...ks);
  const smin=Math.min(...sils), smax=Math.max(...sils);
  const sx=k=>padL+((k-kmin)/((kmax-kmin)||1))*plotW;
  const sy=s=>padT+plotH-((s-smin)/((smax-smin)||1))*plotH;
  let d='';
  ks.forEach((k,i)=>{ const x=sx(k), y=sy(sils[i]); d+=(i===0?'M':'L')+x.toFixed(1)+' '+y.toFixed(1)+' '; });
  let out=`<path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>`;
  ks.forEach((k,i)=>{
    const x=sx(k), y=sy(sils[i]);
    const isChosen = k===chosen;
    out+=`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${isChosen?4.5:2.5}" fill="${isChosen?'var(--accent)':'#bba98a'}"/>`;
    out+=`<text x="${x.toFixed(1)}" y="${H-6}" text-anchor="middle">${k}</text>`;
    if(isChosen) out+=`<text x="${x.toFixed(1)}" y="${(y-8).toFixed(1)}" text-anchor="middle" font-weight="600" fill="var(--accent)">chosen</text>`;
  });
  out+=`<text x="0" y="${padT+4}">${smax.toFixed(2)}</text><text x="0" y="${padT+plotH}">${smin.toFixed(2)}</text>`;
  svg.innerHTML=out;
}

function buildClusterBreakdown(){
  const el=document.getElementById('clusterBreakdown');
  el.innerHTML = DATA.clusterBreakdown.map(cb=>`
    <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:4px">
      <div style="font-size:11px;font-weight:600;display:flex;align-items:center;gap:6px">
        <span style="width:8px;height:8px;border-radius:50%;background:${cb.color}"></span>${cb.cluster}
      </div>
      ${cb.minerals.map(([m,p])=>`
        <div style="display:grid;grid-template-columns:70px minmax(0,1fr) 36px;gap:5px;align-items:center;font-size:10px">
          <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m}</span>
          <div style="background:var(--bg);border-radius:5px;height:7px;overflow:hidden;border:1px solid var(--line)"><div style="height:100%;background:${cb.color};width:${Math.max(p,1)}%"></div></div>
          <span>${p.toFixed(1)}%</span>
        </div>`).join('')}
    </div>`).join('');
}
const DATA = __DATA_JSON__;
const holeIds = DATA.holeIds;
const names = DATA.clusterNames;
const cols = DATA.clusterCols;

const rail = document.getElementById('holeList');
document.getElementById('railCount').textContent = holeIds.length + " holes";
holeIds.forEach(id=>{
  const d = DATA.holes[id];
  const btn = document.createElement('button');
  btn.className='hole-btn'; btn.dataset.id=id; btn.setAttribute('aria-pressed','false');
  btn.innerHTML = `<span style="display:flex;align-items:center;gap:6px;min-width:0"><span class="dot" style="background:${cols[d.dominant]}"></span><span class="mono">${id}</span></span><span class="pct">${d.composition[d.dominant].toFixed(0)}%</span>`;
  btn.addEventListener('click',()=>select(id));
  rail.appendChild(btn);
});

function pctBar(name,val,color){
  return `<div class="bar-row"><div class="name"><span class="dot" style="background:${color}"></span>${name}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(val,0.4)}%;background:${color}"></div></div><div class="val">${val.toFixed(1)}%</div></div>`;
}

function drawWaveChart(highlightIdx){
  const svg=document.getElementById('waveChart');
  const W=260,H=220,padL=8,padR=8,padT=8,padB=18;
  const plotW=W-padL-padR, plotH=H-padT-padB;
  const wl=DATA.wavelengths, spectra=DATA.meanSpectra;
  let ymin=Infinity,ymax=-Infinity;
  spectra.forEach(s=>s.forEach(v=>{if(v<ymin)ymin=v; if(v>ymax)ymax=v;}));
  const xmin=wl[0], xmax=wl[wl.length-1];
  let out=`<line x1="${padL}" y1="${padT+plotH}" x2="${padL+plotW}" y2="${padT+plotH}" stroke="var(--line)"/>`;
  out+=`<text x="${padL}" y="${H-4}">${Math.round(xmin)} nm</text><text x="${padL+plotW-40}" y="${H-4}">${Math.round(xmax)} nm</text>`;
  spectra.forEach((s,idx)=>{
    let d='';
    for(let i=0;i<s.length;i++){
      const x=padL+((wl[i]-xmin)/(xmax-xmin))*plotW;
      const y=padT+plotH-((s[i]-ymin)/(ymax-ymin))*(plotH-6);
      d+=(i===0?'M':'L')+x.toFixed(1)+' '+y.toFixed(1)+' ';
    }
    const w = idx===highlightIdx?2.2:1;
    const op = idx===highlightIdx?1:0.35;
    out+=`<path d="${d}" fill="none" stroke="${cols[idx]}" stroke-width="${w}" opacity="${op}"/>`;
  });
  svg.innerHTML=out;
}

function drawDepthChart(profile){
  const svg=document.getElementById('depthChart');
  const W=240,H=240,padL=40,padR=6,padT=6,padB=6;
  const plotW=W-padL-padR, plotH=H-padT-padB;
  const bins=profile.bins, n=bins.length;
  const rowH=plotH/n;
  let out='';
  for(let i=0;i<n;i++){
    const y=padT+i*rowH;
    const row=bins[i];
    if(row===null){
      out+=`<rect x="${padL}" y="${y.toFixed(1)}" width="${plotW}" height="${(rowH+0.5).toFixed(1)}" fill="var(--line)" opacity="0.5"/>`;
      continue;
    }
    let x=padL;
    row.forEach((pct,ci)=>{
      const w=(pct/100)*plotW;
      out+=`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${(rowH+0.5).toFixed(1)}" fill="${cols[ci]}"/>`;
      x+=w;
    });
  }
  const dmin=profile.depth_min, dmax=profile.depth_max;
  [0,0.5,1].forEach(t=>{
    const y=padT+t*plotH;
    const depthVal=dmin+t*(dmax-dmin);
    out+=`<text x="0" y="${(y+3).toFixed(1)}">${depthVal.toFixed(0)} m</text>`;
  });
  out+=`<rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="none" stroke="var(--line)"/>`;
  svg.innerHTML=out;
}

function select(id){
  document.querySelectorAll('.hole-btn').forEach(b=>b.setAttribute('aria-pressed', b.dataset.id===id?'true':'false'));
  const d = DATA.holes[id];
  document.getElementById('holeName').textContent = id;
  document.getElementById('holeMeta').textContent = "k=3";
  document.getElementById('summaryText').innerHTML =
    `Depth: <b>${d.depth_min.toFixed(1)}\u2013${d.depth_max.toFixed(1)} m</b> (${d.n_points.toLocaleString()} pts). ` +
    `Dominant: <b>${names[d.dominant]}</b> (${d.composition[d.dominant].toFixed(1)}%). ` +
    `Top mineral: <b>${d.top_mineral}</b> (${d.top_mineral_pct.toFixed(1)}%). ` +
    `<b>${d.n_minerals}</b> minerals found. ` +
    (d.similar.length? `Closest: <b>${d.similar[0][0]}</b> (${d.similar[0][1].toFixed(0)}%).` : "");
  document.getElementById('bars').innerHTML = names.map((n,i)=>pctBar(n, d.composition[i], cols[i])).join('');
  document.getElementById('simlist').innerHTML = d.similar.map(([k,v])=>`<div class="simrow"><span class="mono">${k}</span><div class="simtrack"><div class="simfill" style="width:${v.toFixed(0)}%"></div></div><span class="simval">${v.toFixed(0)}%</span></div>`).join('');
  document.getElementById('minlist').innerHTML = d.top_minerals.map(([k,v])=>`<div class="minrow"><span class="mono">${k}</span><div class="mintrack"><div class="minfill" style="width:${Math.max(v,0.4)}%;background:${cols[d.dominant]}"></div></div><span>${v.toFixed(1)}%</span></div>`).join('');
  document.getElementById('grp1List').innerHTML = (d.grp1_composition && d.grp1_composition.length)
  ? d.grp1_composition.map(([k,v])=>`<div class="minrow"><span class="mono">${k}</span><div class="mintrack"><div class="minfill" style="width:${Math.max(v,0.4)}%;background:#8a5a34"></div></div><span>${v.toFixed(1)}%</span></div>`).join('')
  : '<span style="color:var(--ink-dim)">Not available for this hole.</span>';

const whInfo = document.getElementById('wholeHoleInfo');
whInfo.innerHTML = (d.whole_hole_cluster === null || d.whole_hole_cluster === undefined)
  ? '<span style="color:var(--ink-dim)">Not available for this hole.</span>'
  : `Whole-hole cluster: <b>${d.whole_hole_cluster}</b><br>Valid-label coverage: <b>${(d.qc_valid_pct ?? 0).toFixed(1)}%</b>`;
  drawWaveChart(d.dominant);
  drawDepthChart(d.depth_profile);
}
select(holeIds[0]);
drawKChart();
buildClusterBreakdown();
</script>
</body></html>"""

# ---------------------------------------------------------------------------
# Example usage (not run automatically on import):
#
if __name__ == "__main__":
    from hylogger.config import AppConfig

    holes = load_all_tir_holes()
    holes = remove_duplicate_depths(holes)
    holes = clean_tir_holes(holes)
    holes = decode_tir_codes(holes)
    pca_result = run_tir_pca(holes)
    k_scan = cluster_tir_pca(pca_result)              # scans k=2..8, gives the real silhouette curve
    result = cluster_tir_pca(pca_result, n_clusters=3)  # commits to k=3 for the actual clustering
    result["k_scores"] = k_scan["k_scores"]             # give the dashboard the full curve, not just k=3
    describe_tir_clusters(result)
    similarity_df = hole_similarity_matrix(result)

    config = AppConfig.load()
    wh_inputs, wh_result = run_whole_hole_baseline(config)
    check_baseline_stability(wh_inputs)
    check_baseline_scaling_sensitivity(wh_inputs)

    export_tir_client_dashboard(
        holes, result, similarity_df,
        whole_hole_inputs=wh_inputs, whole_hole_result=wh_result,
    )
# ---------------------------------------------------------------------------

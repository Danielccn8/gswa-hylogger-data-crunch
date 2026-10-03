"""
YOU CAN COPY PASTE THIS ENTIRE CODE TO ANY LLM TO SUMMARIZE WHAT EACH STEP DO TO HELP BETTER UNDERSTANDING

TIR-focused pipeline for the 20-hole team cohort (team_raw_tsg_20).

Flow:
  0. Download the shared 20-hole cohort (run once)
  1. Check TIR data availability across the cohort
  2. Extract TIR spectra + scalar columns per hole
  3. Clean: retain repeated readings, replace sentinels, filter by Final Mask
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
from .preparation import gate_readings

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

def check_tir_availability() -> pd.DataFrame:
    """
    For every hole in HOLE_IDS, run `hylogger processed preview` and count
    how many log rows belong to the TIR region specifically (not SWIR).
    """
    rows = []
    for hole_id in HOLE_IDS:
        result = subprocess.run(
            ["hylogger", "processed", "preview", hole_id],
            capture_output=True, text=True,
        )
        output = result.stdout
        total_match = re.search(r"(\d+)\s+displayed logs", output)
        total_logs = int(total_match.group(1)) if total_match else 0
        tir_lines = [line for line in output.splitlines() if re.search(r"\bTIR\b", line)]
        swir_lines = [line for line in output.splitlines() if re.search(r"\bSWIR\b", line)]
        rows.append({
            "hole_id": hole_id,
            "total_logs": total_logs,
            "tir_log_count": len(tir_lines),
            "swir_log_count": len(swir_lines),
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
        "source_row": np.arange(len(spectra)),
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


def load_all_tir_holes() -> dict:
    """Load TIR data for every hole in HOLE_IDS."""
    holes = {}
    for hole_id in HOLE_IDS:
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
    """Report repeated metadata without discarding different spectral readings."""
    for hole_id, hole in holes.items():
        metadata = hole["metadata"]
        repeated = metadata.drop(columns="source_row", errors="ignore").duplicated(keep=False)
        print(f"{hole_id}: {int(repeated.sum())} repeated-metadata rows retained")
    return holes


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

        paired = metadata.rename(columns={"Final Mask": "final_mask"}).copy()
        paired["source_row"] = np.arange(len(paired))
        accepted, qc = gate_readings(
            paired, depth_column="depth_m", spectra=np.asarray(spectra), require_mask=True
        )
        indices = accepted.source_row.to_numpy()
        new_metadata = metadata.iloc[indices].reset_index(drop=True)
        new_spectra = spectra[indices]

        print(f"{hole_id}: {total_before} -> {len(new_metadata)} rows ({total_before - len(new_metadata)} masked/invalid dropped)")
        cleaned[hole_id] = {**hole, "spectra": new_spectra, "metadata": new_metadata, "qc": qc}
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
# Example usage (not run automatically on import):
#
# holes = load_all_tir_holes()
# holes = remove_duplicate_depths(holes)
# holes = clean_tir_holes(holes)
# holes = decode_tir_codes(holes)
# pca_result = run_tir_pca(holes)
# result = cluster_tir_pca(pca_result, n_clusters=3)
# describe_tir_clusters(result)
# plot_tir_dashboard(result)
# similarity_df = hole_similarity_matrix(result)
# plot_hole_similarity_heatmap(similarity_df)
# ---------------------------------------------------------------------------

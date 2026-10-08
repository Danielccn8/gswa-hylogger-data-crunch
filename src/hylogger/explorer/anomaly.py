"""Interval and hole anomaly scores: review candidates with explicit reasons.

Each component is converted to a percentile rank across the sensor's
intervals, then averaged. A reason is attached when a component is in the top
2%. Scores rank unusualness relative to this dataset; they are not
probabilities of mineralisation or error.

Components
- poor_fit: distance to its own cluster centre over that cluster's median
  distance (Sathi, flag_unusual_intervals)
- isolation: Isolation Forest on the Hellinger features
- downhole_contrast: distance to the median of the surrounding +/-5 m
- rare_mineral: carries a mineral present in under 0.2% of intervals
- chemistry_mismatch: where assays exist, the out-of-fold residual of the
  virtual-assay model (assay disagrees with what the mineralogy predicts)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import rankdata
from sklearn.ensemble import IsolationForest

REASON_PERCENTILE = 0.98
RARE = 0.002
WINDOW = 5


def percentile(values: np.ndarray) -> np.ndarray:
    return (rankdata(values, method="average") / len(values)).astype(np.float32)


def downhole_contrast(meta: pd.DataFrame, x: np.ndarray) -> np.ndarray:
    """Distance from each interval to the median of its +/-WINDOW neighbours in the same hole."""
    out = np.zeros(len(x), dtype=np.float32)
    for _, index in meta.groupby("hole_id").indices.items():
        index = index[np.argsort(meta.depth_from_m.to_numpy()[index])]
        values = x[index].astype(np.float32)
        if len(values) < 2:
            continue
        padded = np.pad(values, ((WINDOW, WINDOW), (0, 0)), constant_values=np.nan)
        windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * WINDOW + 1, axis=0).copy()
        windows[:, :, WINDOW] = np.nan  # exclude the interval itself
        with np.errstate(all="ignore"):
            context = np.nanmedian(windows, axis=2)
        out[index] = np.linalg.norm(values - context, axis=1)
    return np.nan_to_num(out)


def interval_scores(
    meta: pd.DataFrame,
    x: np.ndarray,
    abundance: np.ndarray,
    minerals: list[str],
    relative_distance: np.ndarray,
    train: np.ndarray,
    seed: int = 42,
    chem_z: np.ndarray | None = None,
    chem_analyte: np.ndarray | None = None,
) -> tuple[np.ndarray, list[list[str]], dict]:
    forest = IsolationForest(n_estimators=200, random_state=seed, n_jobs=-1).fit(x[train])
    isolation = -forest.score_samples(x)
    contrast = downhole_contrast(meta, x)
    prevalence = (abundance > 0).mean(axis=0)
    rare_columns = np.flatnonzero(prevalence < RARE)
    rare_share = abundance[:, rare_columns].sum(axis=1) if len(rare_columns) else np.zeros(len(x))
    components = {
        "poor_fit": percentile(relative_distance),
        "isolation": percentile(isolation),
        "downhole_contrast": percentile(contrast),
    }
    stack = list(components.values())
    if chem_z is not None and np.isfinite(chem_z).any():
        chem = np.full(len(x), np.nan, dtype=np.float32)
        known = np.isfinite(chem_z)
        chem[known] = percentile(chem_z[known])
        components["chemistry_mismatch"] = chem
        stack.append(chem)
    with np.errstate(all="ignore"):
        score = np.nanmean(np.vstack(stack), axis=0)
    rare_flag = rare_share >= 0.1
    score = np.where(rare_flag, np.maximum(score, 0.9), score).astype(np.float32)
    reasons: list[list[str]] = [[] for _ in range(len(x))]
    for name, values in components.items():
        for row in np.flatnonzero(np.nan_to_num(values) >= REASON_PERCENTILE):
            if name == "chemistry_mismatch" and chem_analyte is not None:
                reasons[row].append(f"chemistry_mismatch:{chem_analyte[row]}")
            else:
                reasons[row].append(name)
    rare_names = np.array(minerals)[rare_columns] if len(rare_columns) else np.array([])
    for row in np.flatnonzero(rare_flag):
        present = rare_names[abundance[row, rare_columns] > 0]
        reasons[row].append("rare_mineral:" + "/".join(present[:3]))
    card = {
        "components": list(components) + ["rare_mineral"],
        "chemistry_rows": int(np.isfinite(chem_z).sum()) if chem_z is not None else 0,
        "reason_percentile": REASON_PERCENTILE,
        "rare_prevalence": RARE,
        "rare_minerals": rare_names.tolist(),
        "meaning": "Relative unusualness within this dataset; a review candidate, not a finding",
    }
    return score, reasons, card


def data_quality_flags(meta: pd.DataFrame) -> pd.DataFrame:
    """Hole-level support anomalies (e.g. a different processing level), kept separate."""
    support = meta.groupby("hole_id").apply(
        lambda part: part.n_admitted.sum() / max(part.n_total.sum(), 1), include_groups=False
    )
    median = support.median()
    mad = (support - median).abs().median() or 1e-6
    z = (support - median) / (1.4826 * mad)
    return pd.DataFrame({"support": support, "support_z": z})

"""Independent checks of HyLogger-derived results against assay chemistry.

1. Mineral <-> oxide rank correlations (global and median within hole).
2. Cluster chemistry: median analyte per cluster and the share of variance in
   log(analyte) explained by the clusters (eta squared).
These are consistency checks against an external reference, not proof of
geological meaning; the assay tables themselves are unverified.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .chemistry import ANALYTES

PAIRS = [
    # (mineral or group pattern, analyte, expected sign)
    ("group:WHITE-MICA", "K2O", 1),
    ("group:PLAGIOCLASE", "Na2O", 1),
    ("group:SERPENTINE", "MgO", 1),
    ("group:OTHER-MGOH", "MgO", 1),
    ("group:CHLORITE", "MgO", 1),
    ("group:CARBONATE", "CaO", 1),
    ("group:CARBONATE", "LOI", 1),
    ("group:KAOLIN", "Al2O3", 1),
    ("group:SILICA", "SiO2", 1),
    ("group:AMPHIBOLE", "CaO", 1),
    ("group:K-FELDSPAR", "K2O", 1),
    ("group:OXIDE", "Fe2O3T", 1),
    ("group:SULPHIDE", "S", 1),
]


def interval_chemistry_table(
    joined: pd.DataFrame, groups: np.ndarray, group_names: list[str], labels: np.ndarray
) -> pd.DataFrame:
    """One row per assay interval: mean group abundance and majority cluster of its bins."""
    mean_groups = np.vstack([groups[rows].mean(axis=0) for rows in joined.rows])
    majority = [int(np.bincount(labels[rows]).argmax()) for rows in joined.rows]
    table = pd.DataFrame(mean_groups, columns=[f"group:{g}" for g in group_names])
    table["cluster_id"] = majority
    table["hole_id"] = joined.hole_id.to_numpy()
    for analyte in ANALYTES:
        if analyte in joined:
            table[analyte] = joined[analyte].to_numpy()
    return table


def correlations(table: pd.DataFrame) -> list[dict]:
    out = []
    for feature, analyte, sign in PAIRS:
        if feature not in table or analyte not in table:
            continue
        part = table[[feature, analyte, "hole_id"]].dropna()
        if len(part) < 30 or part[feature].nunique() < 3:
            continue
        rho = spearmanr(part[feature], part[analyte]).statistic
        within = [
            spearmanr(p[feature], p[analyte]).statistic
            for _, p in part.groupby("hole_id")
            if len(p) >= 20 and p[feature].nunique() > 2 and p[analyte].nunique() > 2
        ]
        out.append(
            {
                "feature": feature.split(":", 1)[1],
                "analyte": analyte,
                "expected": "positive" if sign > 0 else "negative",
                "n": int(len(part)),
                "holes": int(part.hole_id.nunique()),
                "rho": round(float(rho), 3),
                "within_hole_median_rho": round(float(np.nanmedian(within)), 3) if within else None,
                "within_hole_count": len(within),
            }
        )
    return out


def cluster_chemistry(table: pd.DataFrame) -> tuple[dict[int, dict], list[dict]]:
    per_cluster: dict[int, dict] = {}
    for cluster, part in table.groupby("cluster_id"):
        per_cluster[int(cluster)] = {
            "n_assays": int(len(part)),
            **{
                analyte: round(float(part[analyte].median()), 4)
                for analyte in ANALYTES
                if analyte in part and part[analyte].notna().sum() >= 10
            },
        }
    separation = []
    for analyte in ANALYTES:
        if analyte not in table:
            continue
        part = table[["cluster_id", analyte]].dropna()
        part = part[part[analyte] > 0]
        if len(part) < 50 or part.cluster_id.nunique() < 2:
            continue
        values = np.log10(part[analyte])
        grand = values.mean()
        between = sum(len(g) * (g.mean() - grand) ** 2 for _, g in values.groupby(part.cluster_id))
        total = ((values - grand) ** 2).sum()
        separation.append(
            {
                "analyte": analyte,
                "n": int(len(part)),
                "eta_squared": round(float(between / total), 3),
            }
        )
    return per_cluster, sorted(separation, key=lambda r: -r["eta_squared"])

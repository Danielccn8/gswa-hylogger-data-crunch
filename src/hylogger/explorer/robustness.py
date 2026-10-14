"""Robustness of the interval clustering: transfer to unseen districts and interval width.

District transfer: whole districts (5 km groups) are withheld. Feature schema,
rare pooling and K are learned on development districts only. Held intervals
with unseen mineral mass are reported as unassigned. Quantization distances
describe transfer stability, not mineral accuracy or geological validation.
Optional ARI against a full fit is explicitly a transductive reference.

Interval width: a random sample of holes is re-prepared at 0.5 m and 2 m.
For each width the K rule is re-applied and hole fingerprints are recomputed;
the Mantel-style Spearman correlation between hole distance matrices says
whether the whole-hole similarity picture depends on the 1 m choice.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import adjusted_rand_score

from . import clusters, fingerprints
from .data import (
    DEFAULT_SUPPORT_POLICY,
    FrozenFeatureSchema,
    IntervalMatrix,
    balanced_sample,
    model_features,
    support_eligibility,
)
from .features import MissingLogs, interval_features, select_dataset


def _supported_fit(x: np.ndarray, k: int, seed: int):
    """The final ten-start fit must satisfy the same gate as K selection."""
    model = clusters._kmeans(x, k, seed)
    counts = np.bincount(model.labels_, minlength=k)
    if len(np.unique(model.labels_)) != k:
        raise clusters.NoValidClusteringError("Final robustness fit collapsed the selected K")
    if counts.min() / len(x) < clusters.MIN_SHARE:
        raise clusters.NoValidClusteringError("Final robustness fit failed minimum cluster share")
    return model


def district_transfer(
    data: IntervalMatrix,
    k: int,
    district: dict[str, int],
    repeats: int = 3,
    share: float = 0.2,
    seed: int = 42,
    per_hole: int = 150,
    max_train_rows: int = 12000,
    silhouette_rows: int = 3000,
    include_transductive_reference: bool = False,
) -> dict:
    if (
        not 0 < share < 1
        or repeats < 1
        or per_hole < 1
        or max_train_rows < 3
        or silhouette_rows < 3
    ):
        raise ValueError("Expected positive repeat/sample limits and held share in (0,1)")
    holes = data.meta.hole_id
    mapped = holes.map(district)
    located = mapped.notna().to_numpy()
    hole_district = mapped.fillna(-1).to_numpy()
    rng = np.random.default_rng(seed)
    unique = np.unique(hole_district[located])
    base = {
        "version": "district-development-only-v2",
        "source_full_fit_k": k,
        "scope": (
            "Unsupervised district transfer stability; "
            "not geological validation or predictive accuracy"
        ),
        "split_unit": "whole district; all intervals from each hole stay in one partition",
        "unlocated_intervals_excluded": int((~located).sum()),
        "unlocated_holes_excluded": int(holes[~located].nunique()),
        "sampling": {
            "per_hole": per_hole,
            "max_train_rows": max_train_rows,
            "silhouette_rows": silhouette_rows,
            "seed": seed,
        },
        "quality_policy": data.quality_report.get("policy"),
        "mean_ari": None,
        "min_ari": None,
    }
    if len(unique) < 2:
        return {**base, "status": "insufficient_districts", "repeats": []}
    full_labels = None
    if include_transductive_reference:
        x, _ = model_features(data)
        train_all = balanced_sample(holes, per_hole, seed)
        if len(train_all) > max_train_rows:
            train_all = np.sort(rng.choice(train_all, max_train_rows, replace=False))
        full = clusters._kmeans(x[train_all], k, seed)
        full_labels, _, _ = clusters.assign(x, full.cluster_centers_, np.arange(k))
    rows = []
    for repeat in range(repeats):
        held_districts, covered = set(), 0
        for d in rng.permutation(unique)[:-1]:  # always preserve a development district
            held_districts.add(d)
            covered = np.isin(hole_district[located], list(held_districts)).mean()
            if covered >= share:
                break
        held = located & np.isin(hole_district, list(held_districts))
        development = located & ~held
        development_rows = np.flatnonzero(development)
        held_rows = np.flatnonzero(held)
        row = {
            "repeat": repeat,
            "held_district_ids": sorted(int(d) for d in held_districts),
            "held_districts": len(held_districts),
            "held_holes": int(holes[held].nunique()),
            "held_intervals": int(held.sum()),
            "development_holes": int(holes[development].nunique()),
            "development_intervals": int(development.sum()),
        }
        try:
            schema = FrozenFeatureSchema.fit(data, development_rows)
            transformed = schema.transform(data)
            train = development_rows[balanced_sample(holes[development], per_hole, seed + repeat)]
            if len(train) > max_train_rows:
                train = np.sort(rng.choice(train, max_train_rows, replace=False))
            scores = clusters.score_k(
                transformed.x[train],
                clusters.FINE_K,
                seed + repeat,
                sample=silhouette_rows,
                fit_rows=max_train_rows,
            )
            selected_k = clusters.choose_k(scores)
            model = _supported_fit(transformed.x[train], selected_k, seed + repeat)
            _, _, train_distance = clusters.assign(
                transformed.x[train], model.cluster_centers_, np.arange(selected_k)
            )
            train_squared = np.square(train_distance)
            train_hole_means = pd.Series(train_squared).groupby(holes.iloc[train].to_numpy()).mean()
            assigned_rows = held_rows[transformed.assignable[held_rows]]
            row.update(
                {
                    "selected_k": selected_k,
                    "k_scores": scores.to_dict("records"),
                    "schema": schema.to_dict(),
                    "schema_fingerprint": schema.fingerprint,
                    "training_rows": int(len(train)),
                    "training_holes": int(holes.iloc[train].nunique()),
                    "development_holes_not_sampled": int(
                        holes[development].nunique() - holes.iloc[train].nunique()
                    ),
                    "development_mean_squared_distance": float(train_squared.mean()),
                    "development_hole_balanced_mean_squared_distance": float(
                        train_hole_means.mean()
                    ),
                    "held_assigned_intervals": int(len(assigned_rows)),
                    "held_unassigned_intervals": int(len(held_rows) - len(assigned_rows)),
                    "held_unseen_mass_mean": float(transformed.unseen_mass[held_rows].mean()),
                    "held_unseen_minerals": sorted(
                        {
                            m
                            for i, m in enumerate(data.minerals)
                            if m not in schema.mineral_to_feature
                            and np.any(data.abundance[held_rows, i] > 0)
                        }
                    ),
                }
            )
            if len(assigned_rows):
                labels, _, distance = clusters.assign(
                    transformed.x[assigned_rows], model.cluster_centers_, np.arange(selected_k)
                )
                squared = np.square(distance)
                by_hole = pd.Series(squared).groupby(holes.iloc[assigned_rows].to_numpy()).mean()
                row.update(
                    {
                        "status": "evaluated",
                        "held_mean_squared_distance": float(squared.mean()),
                        "held_hole_balanced_mean_squared_distance": float(by_hole.mean()),
                        "held_assigned_holes": int(len(by_hole)),
                        "held_cluster_counts": np.bincount(labels, minlength=selected_k).tolist(),
                        "held_to_development_distance_ratio": float(
                            squared.mean() / train_squared.mean()
                        )
                        if train_squared.mean() > 0
                        else None,
                    }
                )
                if full_labels is not None:
                    row["transductive_ari_vs_full_fit"] = float(
                        adjusted_rand_score(full_labels[assigned_rows], labels)
                    )
            else:
                row["status"] = "no_assignable_held_intervals"
        except ValueError as exc:
            row.update({"status": "no_valid_model", "reason": str(exc)})
        rows.append(row)
    aris = [r["transductive_ari_vs_full_fit"] for r in rows if "transductive_ari_vs_full_fit" in r]
    return {
        **base,
        "status": "evaluated"
        if any(r["status"] == "evaluated" for r in rows)
        else "no_evaluated_splits",
        "repeats": rows,
        "transductive_reference": {
            "enabled": include_transductive_reference,
            "meaning": (
                "Agreement with a model fitted using held districts; not independent accuracy"
            ),
            "mean_ari": float(np.mean(aris)) if aris else None,
            "min_ari": min(aris) if aris else None,
        },
    }


def _matrix_from_tables(
    tables: list[pd.DataFrame], groups: dict[str, str], sensor: str
) -> IntervalMatrix:
    table = pd.concat(tables, ignore_index=True)
    eligible, report = support_eligibility(table, DEFAULT_SUPPORT_POLICY)
    table = table.loc[eligible].reset_index(drop=True)
    minerals = sorted({m for item in table.minerals for m in item})
    index = {m: i for i, m in enumerate(minerals)}
    abundance = np.zeros((len(table), len(minerals)), dtype=np.float32)
    for row, item in enumerate(table.minerals):
        for mineral, value in item.items():
            abundance[row, index[mineral]] = value
    columns = [
        "feature_set_id",
        "bin",
        "hole_id",
        "depth_from_m",
        "depth_to_m",
        "n_total",
        "n_admitted",
    ]
    columns += [
        c
        for c in ("representation", "mask_status", "diagnostic_fraction", "error_mean")
        if c in table
    ]
    meta = table[columns]
    totals = abundance.sum(axis=1, keepdims=True)
    abundance = np.divide(abundance, totals, out=np.zeros_like(abundance), where=totals > 0)
    return IntervalMatrix(sensor, meta, minerals, abundance, groups, report)


def _hole_distances(
    data: IntervalMatrix,
    seed: int,
    per_hole: int = 150,
    max_train_rows: int = 12000,
    silhouette_rows: int = 3000,
) -> tuple[list[str], np.ndarray, int]:
    data = _width_supported(data)
    x, _ = model_features(data)
    train = balanced_sample(data.meta.hole_id, per_hole, seed)
    if len(train) > max_train_rows:
        train = np.sort(np.random.default_rng(seed).choice(train, max_train_rows, replace=False))
    k = clusters.choose_k(
        clusters.score_k(
            x[train], clusters.FINE_K, seed, sample=silhouette_rows, fit_rows=max_train_rows
        )
    )
    model = _supported_fit(x[train], k, seed)
    labels, _, _ = clusters.assign(x, model.cluster_centers_, np.arange(k))
    share = fingerprints.shares(data.meta.hole_id, labels, k)
    return share.index.tolist(), fingerprints.jensen_shannon(share.to_numpy()), k


def _width_supported(data: IntervalMatrix) -> IntervalMatrix:
    counts = data.meta.groupby("hole_id").size()
    admitted = counts[counts >= fingerprints.MIN_INTERVALS].index
    keep = data.meta.hole_id.isin(admitted).to_numpy()
    report = {
        **data.quality_report,
        "fingerprint_support": {
            "minimum_intervals_per_hole": fingerprints.MIN_INTERVALS,
            "holes_loaded": int(len(counts)),
            "holes_eligible": int(len(admitted)),
            "holes_excluded": int((counts < fingerprints.MIN_INTERVALS).sum()),
            "intervals_excluded": int((~keep).sum()),
            "excluded_holes": [
                {"hole_id": str(h), "eligible_intervals": int(counts[h])}
                for h in counts.index
                if counts[h] < fingerprints.MIN_INTERVALS
            ],
            "meaning": (
                "Compatibility floor matching whole-hole fingerprints; not a confidence guarantee"
            ),
        },
    }
    return IntervalMatrix(
        data.sensor,
        data.meta.loc[keep].reset_index(drop=True),
        data.minerals,
        data.abundance[keep],
        data.mineral_group,
        report,
    )


def width_sensitivity(
    data_dir: Path,
    sensor: str,
    holes: list[str],
    widths: tuple[float, ...] = (0.5, 1.0, 2.0),
    seed: int = 42,
    per_hole: int = 150,
    max_train_rows: int = 12000,
    silhouette_rows: int = 3000,
) -> dict:
    if per_hole < 1 or max_train_rows < 3 or silhouette_rows < 3:
        raise ValueError("Expected positive width sensitivity sample limits")
    sampling = {
        "requested_holes": len(holes),
        "per_hole": per_hole,
        "max_train_rows": max_train_rows,
        "silhouette_rows": silhouette_rows,
        "seed": seed,
    }
    results: dict[float, tuple[list[str], np.ndarray, int]] = {}
    reports = {}
    for width in widths:
        tables, groups = [], {}
        for hole_id in holes:
            source = select_dataset(data_dir / "downloads" / "processed" / hole_id)
            if source is None:
                continue
            try:
                table, report = interval_features(source, hole_id, sensor, width)
            except MissingLogs:
                continue
            tables.append(table)
            groups.update(report["mineral_groups"])
        if tables:
            data = _width_supported(_matrix_from_tables(tables, groups, sensor))
            reports[str(width)] = {"quality": data.quality_report, "intervals": len(data)}
            try:
                results[width] = _hole_distances(
                    data, seed, per_hole, max_train_rows, silhouette_rows
                )
                reports[str(width)]["status"] = "fitted"
            except ValueError as exc:
                reports[str(width)].update({"status": "no_valid_model", "reason": str(exc)})
        else:
            reports[str(width)] = {"status": "no_source_tables"}
    if 1.0 not in results:
        return {
            "status": "no_valid_1m_reference",
            "widths": reports,
            "sampling": sampling,
            "mantel_vs_1m": {},
        }
    names, base, base_k = results[1.0]
    out = {
        "status": "evaluated",
        "holes": len(names),
        "k_by_width": {},
        "mantel_vs_1m": {},
        "widths": reports,
        "comparison_notes": {},
        "sampling": sampling,
        "scope": (
            "Descriptive sensitivity to bin width; same sources are reused, "
            "not independent geological validation"
        ),
    }
    for width, (other_names, matrix, k) in results.items():
        out["k_by_width"][str(width)] = k
        if width == 1.0:
            continue
        common = [h for h in names if h in other_names]
        a = [names.index(h) for h in common]
        b = [other_names.index(h) for h in common]
        upper = np.triu_indices(len(common), 1)
        left, right = base[np.ix_(a, a)][upper], matrix[np.ix_(b, b)][upper]
        if (
            len(common) < 3
            or not np.isfinite(left).all()
            or not np.isfinite(right).all()
            or np.ptp(left) == 0
            or np.ptp(right) == 0
        ):
            out["mantel_vs_1m"][str(width)] = None
            out["comparison_notes"][str(width)] = (
                "Insufficient shared holes, nonfinite or constant distances"
            )
        else:
            rho = spearmanr(left, right).statistic
            out["mantel_vs_1m"][str(width)] = round(float(rho), 3) if np.isfinite(rho) else None
        out["widths"][str(width)]["shared_holes_with_1m"] = len(common)
    return out

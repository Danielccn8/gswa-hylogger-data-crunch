"""Load active interval features as dense matrices for analysis."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
import psycopg

MIN_ADMITTED = 3
MIN_PREVALENCE = 0.002  # minerals in fewer intervals are pooled per group for modelling


@dataclass(frozen=True)
class SupportPolicy:
    """Modelling support policy; these limits do not calibrate mineral accuracy."""

    min_admitted: int = MIN_ADMITTED
    min_diagnostic_fraction: float = 0.5
    allow_primary_frequency: bool = False
    allow_unknown_representation: bool = False

    def __post_init__(self):
        if (
            not isinstance(self.min_admitted, int)
            or self.min_admitted < 1
            or not 0 <= self.min_diagnostic_fraction <= 1
        ):
            raise ValueError("Support limits require a positive count and fraction in [0, 1]")


DEFAULT_SUPPORT_POLICY = SupportPolicy()


def support_eligibility(meta: pd.DataFrame, policy: SupportPolicy = DEFAULT_SUPPORT_POLICY):
    """Return an explicit mask and report without deleting descriptive intervals."""
    total = meta.n_total.to_numpy(float)
    admitted = meta.n_admitted.to_numpy(float)
    fraction = np.divide(admitted, total, out=np.zeros_like(admitted), where=total > 0)
    valid_counts = (
        np.isfinite(total)
        & np.isfinite(admitted)
        & (total > 0)
        & (admitted >= 0)
        & (admitted <= total)
    )
    valid_counts &= (total == np.floor(total)) & (admitted == np.floor(admitted))
    count_ok = valid_counts & (admitted >= policy.min_admitted)
    coverage_ok = (total > 0) & (fraction >= policy.min_diagnostic_fraction)
    fallback = (
        meta.representation.eq("primary_mineral_frequency").to_numpy()
        if "representation" in meta
        else np.zeros(len(meta), dtype=bool)
    )
    unknown = (
        ~meta.representation.isin(
            ["primary_mineral_frequency", "component_weight_mixture"]
        ).to_numpy()
        if "representation" in meta
        else np.ones(len(meta), dtype=bool)
    )
    representation_ok = (~fallback | policy.allow_primary_frequency) & (
        ~unknown | policy.allow_unknown_representation
    )
    eligible = count_ok & coverage_ok & representation_ok
    report = {
        "policy": asdict(policy),
        "policy_meaning": (
            "Minimum count and diagnostic coverage for modelling (default: majority support); "
            "not a calibrated accuracy threshold"
        ),
        "descriptive_intervals": int(len(meta)),
        "eligible_intervals": int(eligible.sum()),
        "excluded_intervals": int((~eligible).sum()),
        "below_count": int((~count_ok).sum()),
        "below_diagnostic_fraction": int((~coverage_ok).sum()),
        "primary_frequency_intervals": int(fallback.sum()),
        "unknown_representation_intervals": int(unknown.sum()),
        "invalid_support_counts": int((~valid_counts).sum()),
        "coverage_sensitivity": {
            str(cut): int((count_ok & (fraction >= cut) & (total > 0) & representation_ok).sum())
            for cut in (0.25, 0.5, 0.75)
        },
        "exclusion_counts_overlap": True,
        "error_policy": (
            "Fit error is descriptive only; no calibrated mineral-specific cutoff is available"
        ),
        "abundance_meaning": (
            "Relative TSG interpretation weights conditional on diagnostic readings, "
            "not mineral mass percent"
        ),
    }
    if "mask_status" in meta:
        report["mask_status_intervals"] = (
            meta.mask_status.fillna("unknown").value_counts().to_dict()
        )
    if "representation" in meta:
        report["representation_intervals"] = meta.representation.value_counts().to_dict()
    return eligible, report


@dataclass
class IntervalMatrix:
    sensor: str
    meta: pd.DataFrame  # one row per interval: feature_set_id, bin, hole_id, depths, n_admitted
    minerals: list[str]
    abundance: np.ndarray  # float32, rows sum to 1
    mineral_group: dict[str, str]
    quality_report: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.meta)


def _copy_csv(conn: psycopg.Connection, sql: str) -> pd.DataFrame:
    buffer = io.BytesIO()
    with (
        conn.cursor() as cur,
        cur.copy(f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER)") as copy,
    ):
        for chunk in copy:
            buffer.write(chunk)
    buffer.seek(0)
    return pd.read_csv(buffer)


def load_intervals(
    conn: psycopg.Connection, sensor: str, policy: SupportPolicy = DEFAULT_SUPPORT_POLICY
) -> IntervalMatrix:
    """Load the modelling cohort; source intervals remain available descriptively."""
    if sensor not in {"swir", "tir", "vnir"}:
        raise ValueError("Expected swir/tir/vnir")
    meta = _copy_csv(
        conn,
        f"""SELECT f.feature_set_id, f.bin, f.hole_id, f.depth_from_m, f.depth_to_m,
                   f.n_total, f.n_admitted, f.error_mean,
                   s.qc->>'mask_status' AS mask_status,
                   CASE WHEN s.qc->>'weights_available' = 'false' THEN 'primary_mineral_frequency'
                        WHEN s.qc->>'weights_available' = 'true' THEN 'component_weight_mixture'
                        ELSE 'unknown' END AS representation
            FROM interval_feature f JOIN active_feature_set a USING (feature_set_id)
            JOIN feature_set s USING (feature_set_id)
            WHERE f.sensor = '{sensor}'
            ORDER BY f.hole_id, f.bin""",
    )
    eligible, report = support_eligibility(meta, policy)
    meta = meta.loc[eligible].reset_index(drop=True)
    long = _copy_csv(
        conn,
        f"""SELECT f.feature_set_id, f.bin, m.key AS mineral, m.value::float8 AS w
            FROM interval_feature f JOIN active_feature_set a USING (feature_set_id),
                 jsonb_each_text(f.minerals) m
            WHERE f.sensor = '{sensor}' AND f.n_admitted >= {policy.min_admitted}""",
    )
    groups = _copy_csv(
        conn,
        f"""SELECT DISTINCT ON (key) key AS mineral, value AS grp
            FROM feature_set s JOIN active_feature_set a USING (feature_set_id),
                 jsonb_each_text(s.qc->'mineral_groups')
            WHERE s.sensor = '{sensor}' ORDER BY key""",
    )
    mineral_group = dict(zip(groups.mineral, groups.grp, strict=True)) if len(groups) else {}
    minerals = sorted(long.mineral.unique().tolist())
    row_index = pd.MultiIndex.from_frame(meta[["feature_set_id", "bin"]])
    rows = row_index.get_indexer(pd.MultiIndex.from_frame(long[["feature_set_id", "bin"]]))
    cols = pd.Index(minerals).get_indexer(long.mineral)
    matrix = np.zeros((len(meta), len(minerals)), dtype=np.float32)
    keep = rows >= 0
    np.add.at(matrix, (rows[keep], cols[keep]), long.w.to_numpy(np.float32)[keep])
    totals = matrix.sum(axis=1, keepdims=True)
    matrix = np.divide(matrix, totals, out=np.zeros_like(matrix), where=totals > 0)
    valid = totals[:, 0] > 0
    return IntervalMatrix(
        sensor,
        meta[valid].reset_index(drop=True),
        minerals,
        matrix[valid],
        mineral_group,
        {
            **report,
            "zero_composition_excluded": int((~valid).sum()),
            "model_intervals": int(valid.sum()),
        },
    )


def model_features(data: IntervalMatrix) -> tuple[np.ndarray, list[str]]:
    """Full-cohort descriptive fit. For validation fit a frozen schema on train rows."""
    schema = FrozenFeatureSchema.fit(data)
    return schema.transform(data).x, list(schema.names)


@dataclass
class FrozenTransform:
    x: np.ndarray
    unseen_mass: np.ndarray
    report: dict

    @property
    def assignable(self) -> np.ndarray:
        # Unknown composition is not silently projected onto a complete simplex.
        return (self.unseen_mass <= 1e-7) & (np.square(self.x).sum(axis=1) > 0)


@dataclass(frozen=True)
class FrozenFeatureSchema:
    names: tuple[str, ...]
    mineral_to_feature: dict[str, str]
    min_prevalence: float = MIN_PREVALENCE
    version: str = "train-only-mineral-pooling-v1"

    @classmethod
    def fit(cls, data: IntervalMatrix, train_rows=None, min_prevalence: float = MIN_PREVALENCE):
        if not 0 <= min_prevalence <= 1:
            raise ValueError("Prevalence must be in [0, 1]")
        abundance = data.abundance if train_rows is None else data.abundance[train_rows]
        _validate_abundance(abundance, len(data.minerals))
        if len(abundance) == 0:
            raise ValueError("Cannot fit a feature schema without training rows")
        prevalence = (abundance > 0).mean(axis=0)
        mapping, common, pooled = {}, [], set()
        for mineral, frequency in zip(data.minerals, prevalence, strict=True):
            if frequency == 0:
                continue
            name = (
                mineral
                if frequency >= min_prevalence
                else f"{data.mineral_group.get(mineral, 'OTHER')} (rare)"
            )
            mapping[mineral] = name
            if frequency >= min_prevalence:
                common.append(name)
            else:
                pooled.add(name)
        if not mapping:
            raise ValueError("No observed minerals in training rows")
        return cls(tuple(common + sorted(pooled)), mapping, min_prevalence)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict):
        return cls(
            tuple(value["names"]),
            dict(value["mineral_to_feature"]),
            float(value["min_prevalence"]),
            value["version"],
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()

    def transform(self, data: IntervalMatrix) -> FrozenTransform:
        _validate_abundance(data.abundance, len(data.minerals))
        out = np.zeros((len(data), len(self.names)), dtype=np.float32)
        unseen = np.zeros(len(data), dtype=np.float32)
        feature_index = {name: i for i, name in enumerate(self.names)}
        unseen_names = []
        for col, mineral in enumerate(data.minerals):
            if mineral in self.mineral_to_feature:
                out[:, feature_index[self.mineral_to_feature[mineral]]] += data.abundance[:, col]
            else:
                unseen += data.abundance[:, col]
                if np.any(data.abundance[:, col] > 0):
                    unseen_names.append(mineral)
        transformed = FrozenTransform(np.sqrt(out), unseen, {})
        transformed.report.update(
            {
                "schema_fingerprint": self.fingerprint,
                "unseen_minerals": unseen_names,
                "unseen_mass_mean": float(unseen.mean()) if len(unseen) else None,
                "rows_with_unseen_mass": int((unseen > 1e-7).sum()),
                "assignable_rows": int(transformed.assignable.sum()),
                "normalization": (
                    "No renormalization; known feature mass plus unseen mass preserves input mass"
                ),
            }
        )
        return transformed


def fit_feature_schema(
    data: IntervalMatrix, train_rows=None, min_prevalence: float = MIN_PREVALENCE
):
    return FrozenFeatureSchema.fit(data, train_rows, min_prevalence)


def _validate_abundance(abundance: np.ndarray, columns: int):
    if abundance.ndim != 2 or abundance.shape[1] != columns:
        raise ValueError("Abundance shape must match mineral names")
    if not np.isfinite(abundance).all() or (abundance < 0).any():
        raise ValueError("Abundances must be finite and nonnegative")
    if len(abundance) and not np.allclose(abundance.sum(axis=1), 1, atol=5e-4, rtol=0):
        raise ValueError("Abundances must sum to one (rounded TSG weights)")


def group_abundance(data: IntervalMatrix) -> tuple[np.ndarray, list[str]]:
    groups = sorted({data.mineral_group.get(m, "UNKNOWN") for m in data.minerals})
    index = {g: i for i, g in enumerate(groups)}
    out = np.zeros((len(data), len(groups)), dtype=np.float32)
    for col, mineral in enumerate(data.minerals):
        out[:, index[data.mineral_group.get(mineral, "UNKNOWN")]] += data.abundance[:, col]
    return out, groups


def balanced_sample(holes: pd.Series, per_hole: int, seed: int) -> np.ndarray:
    """Up to `per_hole` random rows per hole, so long holes do not dominate fitting."""
    rng = np.random.default_rng(seed)
    picks = []
    for _, index in holes.groupby(holes).indices.items():
        picks.append(
            index if len(index) <= per_hole else rng.choice(index, per_hole, replace=False)
        )
    return np.sort(np.concatenate(picks)) if picks else np.array([], dtype=int)

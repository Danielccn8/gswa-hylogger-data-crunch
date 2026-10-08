"""Load active interval features as dense matrices for analysis."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import pandas as pd
import psycopg

MIN_ADMITTED = 3
MIN_PREVALENCE = 0.002  # minerals in fewer intervals are pooled per group for modelling


@dataclass
class IntervalMatrix:
    sensor: str
    meta: pd.DataFrame  # one row per interval: feature_set_id, bin, hole_id, depths, n_admitted
    minerals: list[str]
    abundance: np.ndarray  # float32, rows sum to 1
    mineral_group: dict[str, str]

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


def load_intervals(conn: psycopg.Connection, sensor: str) -> IntervalMatrix:
    """Active diagnostic intervals for one sensor, mineral abundance as a dense matrix."""
    meta = _copy_csv(
        conn,
        f"""SELECT f.feature_set_id, f.bin, f.hole_id, f.depth_from_m, f.depth_to_m,
                   f.n_total, f.n_admitted, f.error_mean
            FROM interval_feature f JOIN active_feature_set a USING (feature_set_id)
            WHERE f.sensor = '{sensor}' AND f.n_admitted >= {MIN_ADMITTED}
            ORDER BY f.hole_id, f.bin""",
    )
    long = _copy_csv(
        conn,
        f"""SELECT f.feature_set_id, f.bin, m.key AS mineral, m.value::float8 AS w
            FROM interval_feature f JOIN active_feature_set a USING (feature_set_id),
                 jsonb_each_text(f.minerals) m
            WHERE f.sensor = '{sensor}' AND f.n_admitted >= {MIN_ADMITTED}""",
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
        sensor, meta[valid].reset_index(drop=True), minerals, matrix[valid], mineral_group
    )


def model_features(data: IntervalMatrix) -> tuple[np.ndarray, list[str]]:
    """Hellinger features: common minerals kept, rare ones pooled into '<GROUP> (rare)'."""
    prevalence = (data.abundance > 0).mean(axis=0)
    common = prevalence >= MIN_PREVALENCE
    names = [m for m, keep in zip(data.minerals, common, strict=True) if keep]
    pooled: dict[str, list[int]] = {}
    for index, (mineral, keep) in enumerate(zip(data.minerals, common, strict=True)):
        if not keep:
            pooled.setdefault(f"{data.mineral_group.get(mineral, 'OTHER')} (rare)", []).append(
                index
            )
    parts = [data.abundance[:, common]]
    for name, indices in sorted(pooled.items()):
        parts.append(data.abundance[:, indices].sum(axis=1, keepdims=True))
        names.append(name)
    return np.sqrt(np.hstack(parts)).astype(np.float32), names


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

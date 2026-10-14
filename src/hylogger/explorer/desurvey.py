"""Minimum-curvature desurvey: downhole (measured) depth to true vertical depth.

WAMEX surveys give depth, dip (negative or positive below horizontal) and
azimuth per station; WAPIMS surveys give measured depth and the operator's TVD.
Between stations the minimum-curvature ratio factor is applied; above the
first station the first orientation is used, and below the last station the
last orientation is extended. Holes without a survey are treated as vertical
(and reported as such).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import psycopg

from .db import read_frame


def tvd_stations(
    depth: np.ndarray, dip: np.ndarray, azimuth: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Measured depth and TVD at each station (plus the collar at 0 m)."""
    order = np.argsort(depth)
    md = np.asarray(depth, float)[order]
    inc = np.radians(90.0 - np.abs(np.asarray(dip, float)[order]))  # from vertical
    az = np.radians(np.nan_to_num(np.asarray(azimuth, float)[order]))
    keep = np.isfinite(md) & np.isfinite(inc) & (md >= 0)
    md, inc, az = md[keep], inc[keep], az[keep]
    if not len(md):
        return np.array([0.0]), np.array([0.0])
    if md[0] > 0:
        md, inc, az = np.r_[0.0, md], np.r_[inc[0], inc], np.r_[az[0], az]
    tvd = np.zeros(len(md))
    for i in range(1, len(md)):
        d_md = md[i] - md[i - 1]
        i1, i2, a1, a2 = inc[i - 1], inc[i], az[i - 1], az[i]
        cos_beta = np.cos(i2 - i1) - np.sin(i1) * np.sin(i2) * (1 - np.cos(a2 - a1))
        beta = np.arccos(np.clip(cos_beta, -1, 1))
        ratio = 1.0 if beta < 1e-6 else 2 / beta * np.tan(beta / 2)
        tvd[i] = tvd[i - 1] + d_md / 2 * (np.cos(i1) + np.cos(i2)) * ratio
    return md, tvd


class Desurvey:
    """Per-hole measured-depth -> TVD interpolators."""

    def __init__(self, stations: dict[str, tuple[np.ndarray, np.ndarray, float]]) -> None:
        # hole -> (md stations, tvd stations, cos(inclination) below the last station)
        self.stations = stations

    def tvd(self, hole_id: str, depth: np.ndarray) -> np.ndarray:
        depth = np.asarray(depth, float)
        if hole_id not in self.stations:
            return depth
        md, tvd, tail = self.stations[hole_id]
        out = np.interp(depth, md, tvd)
        beyond = depth > md[-1]
        out[beyond] = tvd[-1] + (depth[beyond] - md[-1]) * tail
        return out

    def surveyed(self) -> set[str]:
        return set(self.stations)


def load(conn: psycopg.Connection, holes: pd.DataFrame) -> Desurvey:
    """holes: index hole_id with wamex_collar_id and name columns."""
    if conn.execute("SELECT to_regclass('ref_survey')").fetchone()[0] is None:
        return Desurvey({})
    stations: dict[str, tuple[np.ndarray, np.ndarray, float]] = {}
    wamex = read_frame(
        conn,
        'SELECT "CollarId" AS collar, "Depth" AS depth, "Dip" AS dip, "Azimuth" AS az '
        'FROM ref_survey WHERE "Depth" >= 0 AND "Dip" BETWEEN -90 AND 90',
    )
    by_collar = {cid: part for cid, part in wamex.groupby("collar")}
    for hole_id, collar in holes.wamex_collar_id.dropna().astype(int).items():
        part = by_collar.get(collar)
        if part is None or part.dip.abs().max() == 0:
            continue
        md, tvd = tvd_stations(part.depth.to_numpy(), part.dip.to_numpy(), part.az.to_numpy())
        last_dip = part.sort_values("depth").dip.iloc[-1]
        stations[hole_id] = (md, tvd, float(abs(np.sin(np.radians(abs(last_dip))))))
    wapims = read_frame(
        conn,
        'SELECT c."HoleName" AS name, s."MD" AS md, s."TVD" AS tvd FROM ref_wapims_survey s '
        'JOIN ref_wapims_collar c ON c."WellId" = s."WellId" '
        'WHERE s."MD" >= 0 AND s."TVD" >= 0',
    )
    if len(wapims) and "name" in holes:
        key = holes["name"].fillna("").str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
        lookup = dict(zip(key, holes.index, strict=True))
        wapims["k"] = wapims.name.str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
        for k, part in wapims.groupby("k"):
            hole_id = lookup.get(k)
            if hole_id is None or hole_id in stations or len(part) < 2:
                continue
            part = part.sort_values("md").drop_duplicates("md")
            md, tvd = part.md.to_numpy(float), part.tvd.to_numpy(float)
            if md[0] > 0:
                md, tvd = np.r_[0.0, md], np.r_[0.0, tvd]
            slope = (tvd[-1] - tvd[-2]) / max(md[-1] - md[-2], 1e-6)
            stations[hole_id] = (md, tvd, float(np.clip(slope, 0, 1)))
    return Desurvey(stations)

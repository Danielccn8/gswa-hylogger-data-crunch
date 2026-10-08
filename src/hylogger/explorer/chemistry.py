"""Harmonised assay chemistry and depth-overlap joins to HyLogger intervals.

Oxide values come from the *_PCT column when present, else from the element
in ppm with the standard oxide factor. Small negative values, which WAMEX uses
for "below detection limit", become half their absolute value. Negative values
of -1000 or less are reporting codes, not detection limits (e.g. -5000 for "not
analysed" in some company submissions) and become missing; -999 was already
NULL. Physically impossible values (>100 wt% oxide, >1,000,000 ppm) are
dropped. The assay tables are flagged unverified in the package's own metadata.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import psycopg

from .db import read_frame

OXIDES = {
    # oxide: (percent column, element ppm column, ppm -> oxide wt% factor)
    "SiO2": ("SiO2_PCT", "Si_PPM", 2.1393e-4),
    "TiO2": ("TiO2_PCT", "Ti_PPM", 1.6681e-4),
    "Al2O3": ("Al2O3_PCT", "Al_PPM", 1.8895e-4),
    "Fe2O3T": ("Fe2O3T_PCT", "Fe_PPM", 1.4297e-4),
    "MnO": ("MnO_PCT", "Mn_PPM", 1.2912e-4),
    "MgO": ("MgO_PCT", "Mg_PPM", 1.6583e-4),
    "CaO": ("CaO_PCT", "Ca_PPM", 1.3992e-4),
    "Na2O": ("Na2O_PCT", "Na_PPM", 1.3480e-4),
    "K2O": ("K2O_PCT", "K_PPM", 1.2046e-4),
    "P2O5": ("P2O5_PCT", "P_PPM", 2.2914e-4),
}
ELEMENTS = [
    "LOI_PCT",
    "S_PPM",
    "Au_PPM",
    "Cu_PPM",
    "Zn_PPM",
    "Pb_PPM",
    "Ni_PPM",
    "As_PPM",
    "Cr_PPM",
    "U_PPM",
    "Th_PPM",
]
ANALYTES = list(OXIDES) + ["LOI", "S", "Au", "Cu", "Zn", "Pb", "Ni", "As", "Cr", "U", "Th"]


CODE_THRESHOLD = -1000  # at or below: a reporting code, not a detection limit
# Upper bounds (ppm) above which a reported value cannot be a real rock analysis
# here (unit or column errors); roughly the pure-mineral content of the element.
PLAUSIBLE_PPM = {
    "U_PPM": 100_000,
    "Th_PPM": 100_000,
    "Au_PPM": 10_000,
    "Cu_PPM": 400_000,
    "Ni_PPM": 400_000,
    "Zn_PPM": 650_000,
    "Pb_PPM": 860_000,
    "As_PPM": 750_000,
    "Cr_PPM": 680_000,
    "S_PPM": 530_000,
}


def _clean(values: pd.Series, *, percent: bool = False, cap: float | None = None) -> pd.Series:
    values = pd.to_numeric(values, errors="coerce")
    values = values.mask(values <= CODE_THRESHOLD)
    values = values.where(values >= 0, values.abs() / 2)
    return values.mask(values > (cap if cap is not None else 100 if percent else 1_000_000))


def assays(conn: psycopg.Connection, collar_ids: list[int] | None = None) -> pd.DataFrame:
    columns = sorted({c for spec in OXIDES.values() for c in spec[:2]} | set(ELEMENTS))
    select = ", ".join(f'"{c}"' for c in columns)
    where = 'WHERE "ToDepth" > "FromDepth"'
    params = None
    if collar_ids is not None:
        where += ' AND "CollarId" = ANY(%s)'
        params = (collar_ids,)
    raw = read_frame(
        conn,
        f'SELECT "CollarId" AS collar_id, "FromDepth" AS f, "ToDepth" AS t, {select} '
        f"FROM ref_assay {where}",
        params,
    )
    out = raw[["collar_id", "f", "t"]].copy()
    for oxide, (pct, ppm, factor) in OXIDES.items():
        out[oxide] = _clean(raw[pct], percent=True).fillna(_clean(raw[ppm]) * factor)
    for column in ELEMENTS:
        out[column.split("_")[0]] = _clean(
            raw[column], percent=column.endswith("_PCT"), cap=PLAUSIBLE_PPM.get(column)
        )
    return out


def join_intervals(
    meta: pd.DataFrame, chem: pd.DataFrame, hole_collar: dict[str, int]
) -> pd.DataFrame:
    """For each assay interval, the 1 m bins of the same hole it overlaps (by row index)."""
    rows = []
    by_collar = {cid: part for cid, part in chem.groupby("collar_id")}
    for hole_id, index in meta.groupby("hole_id").indices.items():
        collar = hole_collar.get(hole_id)
        if collar is None or collar not in by_collar:
            continue
        part = by_collar[collar]
        start = meta.depth_from_m.to_numpy()[index]
        end = meta.depth_to_m.to_numpy()[index]
        order = np.argsort(start)
        start, end, index = start[order], end[order], index[order]
        for assay in part.itertuples(index=False):
            lo = np.searchsorted(end, assay.f, side="right")
            hi = np.searchsorted(start, assay.t, side="left")
            if hi > lo:
                rows.append((assay, index[lo:hi], hole_id))
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame([r[0] for r in rows])
    frame["rows"] = [r[1] for r in rows]
    frame["hole_id"] = [r[2] for r in rows]
    return frame


def analyte_columns(analyte: str) -> tuple[str | None, str | None, float]:
    """(percent column, ppm column, ppm->percent factor) for one harmonised analyte."""
    if analyte in OXIDES:
        return OXIDES[analyte]
    for column in ELEMENTS:
        if column.split("_")[0] == analyte:
            return (column, None, 1.0) if column.endswith("_PCT") else (None, column, 1.0)
    raise KeyError(analyte)


def analyte_values(conn: psycopg.Connection, analyte: str) -> pd.DataFrame:
    """collar_id, f, t, value for one analyte across the whole package, cleaned."""
    pct, ppm, factor = analyte_columns(analyte)
    columns = [c for c in (pct, ppm) if c]
    raw = read_frame(
        conn,
        'SELECT "CollarId" AS collar_id, "FromDepth" AS f, "ToDepth" AS t, '
        + ", ".join(f'"{c}"' for c in columns)
        + ' FROM ref_assay WHERE "ToDepth" > "FromDepth" AND ('
        + " OR ".join(f'"{c}" IS NOT NULL' for c in columns)
        + ")",
    )
    if pct and ppm:
        value = _clean(raw[pct], percent=True).fillna(_clean(raw[ppm]) * factor)
    elif pct:
        value = _clean(raw[pct], percent=True)
    else:
        value = _clean(raw[ppm], cap=PLAUSIBLE_PPM.get(ppm))
    reported = pd.concat([pd.to_numeric(raw[c], errors="coerce") for c in columns], axis=1)
    reported = reported.notna().any(axis=1) & ~(reported <= CODE_THRESHOLD).any(axis=1)
    out = raw[["collar_id", "f", "t"]].assign(value=value, rejected=reported & value.isna())
    return out[out.value.notna() | out.rejected]

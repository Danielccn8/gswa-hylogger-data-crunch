"""Harmonised assay chemistry and depth-overlap joins to HyLogger intervals.

Oxide values come from the *_PCT column when present, else from the element
in ppm with the standard oxide factor. Negative values, which WAMEX uses for
"below detection", become half their absolute value; -999 was already NULL.
The assay tables are flagged unverified in the package's own metadata.
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
]
ANALYTES = list(OXIDES) + ["LOI", "S", "Au", "Cu", "Zn", "Pb", "Ni", "As", "Cr"]


def _clean(values: pd.Series) -> pd.Series:
    values = pd.to_numeric(values, errors="coerce")
    return values.where(values >= 0, values.abs() / 2)


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
        out[oxide] = _clean(raw[pct]).fillna(_clean(raw[ppm]) * factor)
    for column in ELEMENTS:
        out[column.split("_")[0]] = _clean(raw[column])
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

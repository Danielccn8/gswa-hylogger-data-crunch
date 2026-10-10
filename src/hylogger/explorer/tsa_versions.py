"""Automatic (system) versus user TSA interpretations, summarised per metre.

TSG stores two interpretations of every reading: the system run (sTSAS, sTSAV,
sjCLST), produced automatically, and the user run (uTSAS, uTSAV, ujCLST), which
an analyst may have re-run with a constrained mineral library or edited. The
NVCL service publishes only the user run for Western Australia; both live in the
scalar plane of the raw .bip file, so they are summarised while the package is
on disk. Readings are the same mask-admitted readings used for the raw summary.

Per metre and product: the mineral and group mix of each version (Min1-3 x Wt1-3
normalised per reading, as in features.py), the main mineral of each, the share
of readings whose first mineral agrees, and the overlap of the two mixes.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

# sensor section -> product label -> (system suffix, user suffix) of the snake-case scalars
PRODUCTS = {
    "nir": {"swir": ("s_tsas", "u_tsas"), "vnir": ("s_tsav", "u_tsav")},
    "tir": {"tir": ("sj_clst", "uj_clst")},
}
INVALID = {"", "null", "none", "nan", "-"}


def _valid(names: pd.Series) -> pd.Series:
    return names.notna() & ~names.astype(str).str.strip().str.lower().isin(INVALID)


def _mix(frame: pd.DataFrame, suffix: str, kind: str) -> pd.DataFrame | None:
    """Long table (bin, name, share) of the mean per-reading normalised weights."""
    if f"{kind}1_{suffix}" not in frame.columns:
        return None
    names = [frame.get(f"{kind}{k}_{suffix}") for k in (1, 2, 3)]
    weights = []
    for k, name in enumerate(names, start=1):
        weight = pd.to_numeric(frame.get(f"wt{k}_{suffix}"), errors="coerce")
        if weight is None or name is None:
            weights.append(pd.Series(0.0, index=frame.index))
            continue
        weights.append(weight.where(_valid(name), 0.0).fillna(0.0).clip(lower=0.0))
    weight = pd.concat(weights, axis=1)
    # A first mineral with no usable weight still counts as the whole reading.
    first = _valid(names[0])
    weight.iloc[:, 0] = weight.iloc[:, 0].where(~(first & (weight.sum(axis=1) == 0)), 1.0)
    total = weight.sum(axis=1).replace(0, np.nan)
    parts = []
    for k, name in enumerate(names):
        if name is None:
            continue
        share = weight.iloc[:, k] / total
        keep = _valid(name) & share.gt(0)
        parts.append(
            pd.DataFrame(
                {"bin": frame.bin[keep], "name": name[keep].astype(str), "share": share[keep]}
            )
        )
    if not parts:
        return None
    long = pd.concat(parts)
    readings = frame[first].groupby("bin").size()
    mix = long.groupby(["bin", "name"]).share.sum().div(readings, level="bin").rename("share")
    return mix.reset_index()


def _as_dicts(mix: pd.DataFrame | None, top: int) -> dict[int, dict[str, float]]:
    if mix is None or mix.empty:
        return {}
    out = {}
    for bin_id, part in mix.sort_values("share", ascending=False).groupby("bin", sort=False):
        out[int(bin_id)] = {r.name: round(float(r.share), 4) for r in part.head(top).itertuples()}
    return out


def per_metre(frame: pd.DataFrame, sensor: str, width_m: float = 1.0) -> pd.DataFrame:
    """One row per (product, bin) where both the system and the user versions exist."""
    rows = []
    for product, (system, user) in PRODUCTS.get(sensor, {}).items():
        if f"min1_{system}" not in frame.columns or f"min1_{user}" not in frame.columns:
            continue
        minerals = {v: _as_dicts(_mix(frame, v, "min"), 8) for v in (system, user)}
        groups = {v: _as_dicts(_mix(frame, v, "grp"), 8) for v in (system, user)}
        both = _valid(frame[f"min1_{system}"]) & _valid(frame[f"min1_{user}"])
        same = frame[f"min1_{system}"].astype(str) == frame[f"min1_{user}"].astype(str)
        agree = (same & both).groupby(frame.bin).sum() / both.groupby(frame.bin).sum().replace(
            0, np.nan
        )
        counts = frame.groupby("bin").size()
        for bin_id in sorted(set(minerals[system]) & set(minerals[user])):
            s, u = minerals[system][bin_id], minerals[user][bin_id]
            overlap = sum(min(s.get(k, 0.0), u.get(k, 0.0)) for k in set(s) | set(u))
            s_top, u_top = max(s, key=s.get), max(u, key=u.get)
            rows.append(
                {
                    "product": product,
                    "bin": int(bin_id),
                    "depth_from_m": round(bin_id * width_m, 6),
                    "depth_to_m": round((bin_id + 1) * width_m, 6),
                    "n_readings": int(counts.get(bin_id, 0)),
                    "system_top": s_top,
                    "system_top_share": s[s_top],
                    "user_top": u_top,
                    "user_top_share": u[u_top],
                    "min1_agreement": None
                    if pd.isna(agree.get(bin_id))
                    else round(float(agree[bin_id]), 4),
                    "mix_overlap": round(float(overlap), 4),
                    "system_minerals": json.dumps(s),
                    "user_minerals": json.dumps(u),
                    "system_groups": json.dumps(groups[system].get(bin_id, {})),
                    "user_groups": json.dumps(groups[user].get(bin_id, {})),
                }
            )
    return pd.DataFrame(rows)


def summary(table: pd.DataFrame) -> dict:
    """Per product: metres compared, mean agreement and the most common main-mineral changes."""
    out = {}
    if table.empty:
        return out
    for product, part in table.groupby("product"):
        changed = part[part.system_top != part.user_top]
        changes = (
            changed.groupby(["system_top", "user_top"]).size().sort_values(ascending=False).head(6)
        )
        out[product] = {
            "metres": int(len(part)),
            "min1_agreement": round(float(part.min1_agreement.mean()), 4),
            "mix_overlap": round(float(part.mix_overlap.mean()), 4),
            "metres_main_mineral_changed": int(len(changed)),
            "top_changes": [[s, u, int(n)] for (s, u), n in changes.items()],
        }
    return out

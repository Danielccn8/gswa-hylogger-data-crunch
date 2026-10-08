"""Virtual assays: chemistry predicted from HyLogger mineralogy, and mismatch scores.

Features are the Hellinger mineral abundances of SWIR and TIR for the same 1 m
bin, side by side (a missing sensor stays NaN; gradient boosting handles it).
Each assay interval takes the mean features of the bins it overlaps; the target
is log10 of the harmonised analyte. Skill is measured out-of-fold with whole
holes held out (GroupKFold), so neighbouring metres never leak between train and
test. The fitted model then predicts every bin, including holes without assays.

The out-of-fold residual (observed minus predicted, in robust standard units)
marks assay intervals whose chemistry disagrees with their mineralogy. This is a
review signal: it can reflect minerals HyLogger cannot see (sulphides, oxides in
SWIR, fine quartz), sampling mismatch, or assay errors.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold

TARGETS = ["SiO2", "Al2O3", "Fe2O3T", "MgO", "CaO", "Na2O", "K2O", "LOI", "S", "Ni", "Cr", "U"]
MIN_SAMPLES = 400
MIN_HOLES = 20
FOLDS = 5
MIN_R2 = 0.3  # below this the analyte is reported as not predictable from mineralogy


def combined_features(
    datasets: dict[str, tuple[pd.DataFrame, np.ndarray, list[str]]],
) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    """Union of bins across sensors; columns prefixed by sensor, NaN where a sensor is absent."""
    frames = []
    for sensor, (meta, x, names) in datasets.items():
        frame = pd.DataFrame(x, columns=[f"{sensor}:{n}" for n in names])
        frame["hole_id"] = meta.hole_id.to_numpy()
        frame["bin"] = meta.bin.to_numpy()
        frame["depth_from_m"] = meta.depth_from_m.to_numpy()
        frame["depth_to_m"] = meta.depth_to_m.to_numpy()
        frames.append(frame.set_index(["hole_id", "bin"]))
    joined = frames[0]
    for frame in frames[1:]:
        joined = joined.combine_first(frame)
    joined = joined.sort_index()
    meta = joined[["depth_from_m", "depth_to_m"]].reset_index()
    columns = [c for c in joined.columns if ":" in c]
    return meta, joined[columns].to_numpy(np.float32), columns


def fit(meta: pd.DataFrame, x: np.ndarray, joined: pd.DataFrame, seed: int = 42) -> dict:
    """joined: chemistry.join_intervals output (assay rows with `rows` into meta)."""
    with np.errstate(all="ignore"):
        features = np.vstack([np.nanmean(x[rows], axis=0) for rows in joined.rows])
    holes = joined.hole_id.to_numpy()
    models, skill, oof_z = {}, [], {}
    for analyte in TARGETS:
        if analyte not in joined:
            continue
        y = joined[analyte].to_numpy(float)
        ok = np.isfinite(y) & (y > 0)
        if ok.sum() < MIN_SAMPLES or len(set(holes[ok])) < MIN_HOLES:
            continue
        xa, ya, ga = features[ok], np.log10(y[ok]), holes[ok]
        oof = np.full(len(ya), np.nan)
        folds = GroupKFold(n_splits=min(FOLDS, len(set(ga))))
        for train, test in folds.split(xa, ya, ga):
            model = _model(seed).fit(xa[train], ya[train])
            oof[test] = model.predict(xa[test])
        residual = ya - oof
        mad = np.median(np.abs(residual - np.median(residual))) * 1.4826 or 1e-6
        baseline = np.mean((ya - ya.mean()) ** 2)
        r2 = 1 - np.mean(residual**2) / baseline if baseline else np.nan
        skill.append(
            {
                "analyte": analyte,
                "n": int(ok.sum()),
                "holes": int(len(set(ga))),
                "r2_holdout_holes": round(float(r2), 3),
                "median_abs_error_factor": round(float(10 ** np.median(np.abs(residual))), 3),
            }
        )
        skill[-1]["predictable"] = bool(np.isfinite(r2) and r2 >= MIN_R2)
        if not skill[-1]["predictable"]:
            continue
        z = np.full(len(joined), np.nan)
        z[np.flatnonzero(ok)] = residual / mad
        oof_z[analyte] = z
        models[analyte] = _model(seed).fit(xa, ya)
    return {"models": models, "skill": skill, "z": pd.DataFrame(oof_z, index=joined.index)}


def _model(seed: int) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_iter=250, learning_rate=0.08, max_leaf_nodes=31, min_samples_leaf=40, random_state=seed
    )


def predict(models: dict, x: np.ndarray) -> pd.DataFrame:
    out = {}
    for analyte, model in models.items():
        out[analyte] = (10 ** model.predict(x)).astype(np.float32)
    return pd.DataFrame(out)


def mismatch_by_bin(meta: pd.DataFrame, joined: pd.DataFrame, z: pd.DataFrame) -> pd.DataFrame:
    """Largest |z| per assay interval, spread to the bins it overlaps (max where several)."""
    if z.empty:
        return pd.DataFrame(columns=["hole_id", "bin", "chem_z", "chem_analyte"])
    absz = z.abs()
    has = absz.notna().any(axis=1)
    worst = pd.Series(None, index=absz.index, dtype=object)
    worst[has] = absz[has].idxmax(axis=1)
    value = absz.max(axis=1, skipna=True)
    rows = []
    for rows_idx, score, analyte in zip(joined.rows, value, worst, strict=True):
        if not np.isfinite(score):
            continue
        for row in rows_idx:
            rows.append((row, float(score), analyte))
    if not rows:
        return pd.DataFrame(columns=["hole_id", "bin", "chem_z", "chem_analyte"])
    frame = pd.DataFrame(rows, columns=["row", "chem_z", "chem_analyte"])
    frame = frame.sort_values("chem_z", ascending=False).drop_duplicates("row")
    frame["hole_id"] = meta.hole_id.to_numpy()[frame.row]
    frame["bin"] = meta.bin.to_numpy()[frame.row]
    return frame[["hole_id", "bin", "chem_z", "chem_analyte"]]

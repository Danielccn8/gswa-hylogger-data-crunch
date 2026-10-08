"""Expected mineralogy at a location from nearby holes, with honest validation.

Target: a hole's mean mineral-group composition (SWIR and TIR separately),
overall and per vertical-depth band. Method: inverse-distance weighting of the
k nearest holes (great-circle distance). Validation leaves each hole out
(LOHO) and, separately, its whole district out (LODO); both are compared with
the state-wide and tectonic-region averages. Error is the Hellinger distance
between predicted and observed compositions. Skill = 1 - error / baseline error.

The error-versus-distance table tells the app when a location is beyond the
support of the data (no skill over the regional baseline at that distance).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

EARTH_KM = 6371.0
K = 8
POWER = 2.0
DEPTH_BANDS = [(0, 50), (50, 100), (100, 200), (200, 400), (400, 3000)]
DISTANCE_BINS = [0, 1, 2, 5, 10, 20, 50, 100, 10000]


def hellinger_rows(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    return np.sqrt(np.clip(((np.sqrt(p) - np.sqrt(q)) ** 2).sum(axis=1) / 2, 0, 1))


def idw(distances_km: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    weights = 1 / np.maximum(distances_km, 0.05) ** POWER
    weights = weights / weights.sum()
    mean = (weights[:, None] * values).sum(axis=0)
    spread = np.sqrt((weights[:, None] * (values - mean) ** 2).sum(axis=0))
    return mean, spread


class Interpolator:
    def __init__(
        self, lat: np.ndarray, lon: np.ndarray, values: np.ndarray, holes: list[str]
    ) -> None:
        self.values = values
        self.holes = np.asarray(holes)
        self.tree = BallTree(np.radians(np.c_[lat, lon]), metric="haversine")

    def query(self, lat: float, lon: float, k: int = K, exclude: set[int] | None = None):
        extra = len(exclude or ()) + 1
        count = min(len(self.values), k + extra)
        dist, index = self.tree.query(np.radians([[lat, lon]]), k=count)
        dist, index = dist[0] * EARTH_KM, index[0]
        if exclude:
            keep = ~np.isin(index, list(exclude))
            dist, index = dist[keep], index[keep]
        dist, index = dist[:k], index[:k]
        if not len(index):
            return None
        mean, spread = idw(dist, self.values[index])
        return mean, spread, dist, index


def cross_validate(
    holes: pd.DataFrame, values: np.ndarray, *, district_column: str = "district_id"
) -> tuple[pd.DataFrame, list[dict], dict]:
    """holes: hole_id, lat, lon, district_id, tectonic_region (aligned with values rows)."""
    model = Interpolator(holes.lat.to_numpy(), holes.lon.to_numpy(), values, holes.hole_id.tolist())
    region = holes.tectonic_region.fillna("Unknown").to_numpy()
    district = holes[district_column].to_numpy()
    total = values.sum(axis=0)
    rows = []
    for i in range(len(holes)):
        loho = model.query(holes.lat.iat[i], holes.lon.iat[i], exclude={i})
        same_district = set(np.flatnonzero(district == district[i]).tolist())
        lodo = model.query(holes.lat.iat[i], holes.lon.iat[i], exclude=same_district)
        others = np.ones(len(holes), dtype=bool)
        others[i] = False
        regional = others & (region == region[i])
        global_mean = (total - values[i]) / max(len(holes) - 1, 1)
        region_mean = values[regional].mean(axis=0) if regional.any() else global_mean
        truth = values[i : i + 1]
        rows.append(
            {
                "hole_id": holes.hole_id.iat[i],
                "nearest_km": float(loho[2][0]) if loho else np.nan,
                "loho_error": float(hellinger_rows(loho[0][None], truth)[0]) if loho else np.nan,
                "lodo_nearest_km": float(lodo[2][0]) if lodo else np.nan,
                "lodo_error": float(hellinger_rows(lodo[0][None], truth)[0]) if lodo else np.nan,
                "global_error": float(hellinger_rows(global_mean[None], truth)[0]),
                "region_error": float(hellinger_rows(region_mean[None], truth)[0]),
            }
        )
    table = pd.DataFrame(rows)
    by_distance = _by_distance(table, "nearest_km", "loho_error")
    lodo_by_distance = _by_distance(table, "lodo_nearest_km", "lodo_error")
    summary = {
        "holes": int(len(table)),
        "loho_median_error": round(float(table.loho_error.median()), 4),
        "lodo_median_error": round(float(table.lodo_error.median()), 4),
        "global_baseline_median_error": round(float(table.global_error.median()), 4),
        "region_baseline_median_error": round(float(table.region_error.median()), 4),
        "loho_skill_vs_region": round(
            float(1 - table.loho_error.median() / table.region_error.median()), 3
        ),
        "lodo_skill_vs_region": round(
            float(1 - table.lodo_error.median() / table.region_error.median()), 3
        ),
        "support_limit_km": support_limit(by_distance),
        "support_rule": f"nearest-hole distance bins with skill >= {SKILL_THRESHOLD} vs region",
        "lodo_by_distance": lodo_by_distance,
    }
    return table, by_distance, summary


SKILL_THRESHOLD = 0.15


def support_limit(by_distance: list[dict]) -> float | None:
    """Largest nearest-hole distance up to which IDW clearly beats the regional average.

    Bins are read outward from 0 km; the first bin below SKILL_THRESHOLD ends the support.
    The open-ended last bin never counts (too few, too varied holes).
    """
    limit = None
    for row in by_distance:
        if row["to_km"] > 1000:
            break
        if row["skill_vs_region"] is not None and row["skill_vs_region"] >= SKILL_THRESHOLD:
            limit = row["to_km"]
        else:
            break
    return limit


def _by_distance(table: pd.DataFrame, distance: str, error: str) -> list[dict]:
    rows = []
    binned = pd.cut(table[distance], DISTANCE_BINS, right=False)
    for interval, part in table.groupby(binned, observed=True):
        if len(part) < 5:
            continue
        model_error, base = part[error].median(), part.region_error.median()
        rows.append(
            {
                "from_km": float(interval.left),
                "to_km": float(interval.right),
                "holes": int(len(part)),
                "model_error": round(float(model_error), 4),
                "region_baseline_error": round(float(base), 4),
                "skill_vs_region": round(float(1 - model_error / base), 3) if base else None,
            }
        )
    return rows


def band_compositions(
    meta: pd.DataFrame, groups: np.ndarray, desurvey
) -> dict[tuple[str, int], np.ndarray]:
    """Mean group composition per (hole, depth band) over true vertical depth."""
    out: dict[tuple[str, int], np.ndarray] = {}
    for hole_id, index in meta.groupby("hole_id").indices.items():
        mid = (meta.depth_from_m.to_numpy()[index] + meta.depth_to_m.to_numpy()[index]) / 2
        depth = desurvey.tvd(hole_id, mid)
        for band, (low, high) in enumerate(DEPTH_BANDS):
            members = index[(depth >= low) & (depth < high)]
            if len(members) >= 5:
                out[(hole_id, band)] = groups[members].mean(axis=0)
    return out


def _idw_features(model: Interpolator, lat, lon, exclude: set[int] | None = None):
    found = model.query(lat, lon, exclude=exclude)
    if found is None:
        return None
    mean, spread, dist, _ = found
    return np.r_[mean, spread, dist[0], dist.mean()]


def _fold_features(model, rows, district, local_district, lat, lon, codes, own_district):
    """Features for global row indices; exclusion indices refer to the fold's training set."""
    out = []
    for i in rows:
        exclude = (
            set(np.flatnonzero(local_district == district[i]).tolist()) if own_district else None
        )
        f = _idw_features(model, lat[i], lon[i], exclude)
        out.append(np.r_[lat[i], lon[i], codes[i], f] if f is not None else None)
    return out


def covariate_cv(
    holes: pd.DataFrame, values: np.ndarray, folds: int = 5, seed: int = 42
) -> tuple[dict, pd.DataFrame]:
    """Gradient boosting on location, tectonic region and IDW-from-other-districts features.

    Folds hold out whole districts; inside each fold the IDW features are rebuilt
    from the training districts only, so no test-district target leaks into a feature.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import GroupKFold

    lat, lon = holes.lat.to_numpy(float), holes.lon.to_numpy(float)
    district = holes.district_id.to_numpy()
    region = pd.Categorical(holes.tectonic_region.fillna("Unknown"))
    codes = region.codes.astype(float)
    n, k = values.shape
    oof = np.full((n, k), np.nan)
    for train, test in GroupKFold(n_splits=folds).split(values, groups=district):
        model = Interpolator(
            lat[train], lon[train], values[train], holes.hole_id.iloc[train].tolist()
        )
        local_district = district[train]

        train_rows = _fold_features(model, train, district, local_district, lat, lon, codes, True)
        keep = [j for j, r in enumerate(train_rows) if r is not None]
        xtr = np.vstack([train_rows[j] for j in keep])
        test_rows = _fold_features(model, test, district, local_district, lat, lon, codes, False)
        xte = np.vstack(test_rows)
        for j in range(k):
            reg = HistGradientBoostingRegressor(
                max_iter=200,
                learning_rate=0.06,
                max_leaf_nodes=15,
                min_samples_leaf=20,
                categorical_features=[2],
                random_state=seed,
            ).fit(xtr, values[train][keep, j])
            oof[test, j] = reg.predict(xte)
    oof = np.clip(oof, 0, None)
    oof = oof / np.maximum(oof.sum(axis=1, keepdims=True), 1e-9)
    error = hellinger_rows(oof, values)
    table = pd.DataFrame({"hole_id": holes.hole_id.to_numpy(), "covariate_error": error})
    return {"median_error": round(float(np.median(error)), 4), "folds": folds}, table


def fit_covariate_models(holes: pd.DataFrame, values: np.ndarray, seed: int = 42) -> dict:
    """Final models on all holes (IDW features computed leave-district-out)."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    lat, lon = holes.lat.to_numpy(float), holes.lon.to_numpy(float)
    district = holes.district_id.to_numpy()
    region = pd.Categorical(holes.tectonic_region.fillna("Unknown"))
    model = Interpolator(lat, lon, values, holes.hole_id.tolist())
    rows = []
    for i in range(len(holes)):
        f = _idw_features(
            model, lat[i], lon[i], set(np.flatnonzero(district == district[i]).tolist())
        )
        rows.append(np.r_[lat[i], lon[i], region.codes[i], f])
    x = np.vstack(rows)
    regressors = [
        HistGradientBoostingRegressor(
            max_iter=200,
            learning_rate=0.06,
            max_leaf_nodes=15,
            min_samples_leaf=20,
            categorical_features=[2],
            random_state=seed,
        ).fit(x, values[:, j])
        for j in range(values.shape[1])
    ]
    return {"regressors": regressors, "regions": list(region.categories)}


def covariate_predict(
    bundle: dict, interpolator: Interpolator, lat: float, lon: float, region: str | None
) -> np.ndarray:
    f = _idw_features(interpolator, lat, lon)
    code = bundle["regions"].index(region) if region in bundle["regions"] else -1
    x = np.r_[lat, lon, code, f][None, :]
    out = np.clip(np.array([reg.predict(x)[0] for reg in bundle["regressors"]]), 0, None)
    return out / max(out.sum(), 1e-9)

"""Run the full analysis and store versioned, staged results.

    python -m hylogger.explorer.analysis [--no-promote] [--no-raw]

Per sensor (SWIR, TIR): interval clusters (fine + coarse), domains, anomaly
scores, assay validation and whole-hole fingerprints/similarity. Then a
combined SWIR+TIR hole view, raw-spectra whole-hole views, and the spatial
prediction cross-validation. Every result is written under a new run_id and
promoted only when `promote` is true.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from . import (
    anomaly,
    chemistry,
    clusters,
    desurvey,
    domains,
    fingerprints,
    prediction,
    rawcluster,
    validation,
    virtual_assay,
)
from .data import balanced_sample, group_abundance, load_intervals, model_features
from .db import connect, copy_frame, read_frame, resolve_product

SENSORS = ("swir", "tir")
SEED = 42


def _run_id(kind: str, sensor: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{kind}-{sensor}-{stamp}-{hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:6]}"


def _store_run(
    conn: psycopg.Connection, run_id: str, kind: str, sensor: str, card: dict, path: Path
) -> None:
    conn.execute(
        "INSERT INTO analysis_run(run_id,kind,sensor,status,card,product_path) "
        "VALUES (%s,%s,%s,'staged',%s,%s)",
        (run_id, kind, sensor, Jsonb(json.loads(json.dumps(card, default=_default))), str(path)),
    )


def _default(value: object) -> object:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if np.isnan(value) else float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, pd.Interval):
        return str(value)
    return str(value)


def promote(conn: psycopg.Connection, run_id: str) -> str:
    with conn.transaction():
        row = conn.execute(
            "SELECT kind, sensor FROM analysis_run WHERE run_id=%s", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown run {run_id}")
        conn.execute(
            "UPDATE analysis_run SET status='archived' "
            "WHERE kind=%s AND sensor=%s AND status='active'",
            row,
        )
        conn.execute("UPDATE analysis_run SET status='active' WHERE run_id=%s", (run_id,))
    return run_id


def hole_attributes(conn: psycopg.Connection) -> pd.DataFrame:
    holes = read_frame(
        conn,
        "SELECT hole_id, name, lat, lon, district_id, tectonic_region, wamex_collar_id, commodity "
        "FROM explorer_hole",
    )
    return holes.set_index("hole_id")


def interval_run(
    conn,
    data_dir: Path,
    sensor: str,
    holes: pd.DataFrame,
    log,
    data=None,
    chem_mismatch: pd.DataFrame | None = None,
) -> dict | None:
    data = data if data is not None else load_intervals(conn, sensor)
    if len(data) < 500 or data.meta.hole_id.nunique() < 3:
        log(f"{sensor}: too few intervals ({len(data)}) for clustering")
        return None
    log(f"{sensor}: {len(data):,} intervals from {data.meta.hole_id.nunique()} holes")
    x, feature_names = model_features(data)
    train = balanced_sample(data.meta.hole_id, 150, SEED)
    fit = clusters.fit(x, train, data.abundance, data.minerals, SEED)
    log(f"{sensor}: k_fine={fit['k']} k_coarse={fit['card']['k_coarse']}")
    domain_id, domain_table = domains.all_domains(
        data.meta, x, data.abundance, data.minerals, fit["labels"]
    )
    chem_z = chem_analyte = None
    if chem_mismatch is not None and len(chem_mismatch):
        mapped = data.meta[["hole_id", "bin"]].merge(
            chem_mismatch, on=["hole_id", "bin"], how="left"
        )
        chem_z = mapped.chem_z.to_numpy(float)
        chem_analyte = mapped.chem_analyte.to_numpy(object)
    score, reasons, anomaly_card = anomaly.interval_scores(
        data.meta,
        x,
        data.abundance,
        data.minerals,
        fit["relative_distance"],
        train,
        SEED,
        chem_z=chem_z,
        chem_analyte=chem_analyte,
    )
    quality = anomaly.data_quality_flags(data.meta)

    # Assay validation.
    groups, group_names = group_abundance(data)
    collars = holes.wamex_collar_id.dropna().astype(int).to_dict()
    chem = chemistry.assays(conn, sorted(set(collars.values())))
    joined = chemistry.join_intervals(data.meta, chem, collars)
    validation_card: dict = {"assay_intervals": int(len(joined))}
    cluster_chem: dict[int, dict] = {}
    if len(joined):
        table = validation.interval_chemistry_table(joined, groups, group_names, fit["labels"])
        validation_card["correlations"] = validation.correlations(table)
        cluster_chem, validation_card["cluster_separation"] = validation.cluster_chemistry(table)
        validation_card["holes_with_assays"] = int(table.hole_id.nunique())

    run_id = _run_id("interval", sensor)
    path = data_dir / "products" / "analysis" / run_id
    path.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path / "model.npz",
        centres=fit["centres"],
        parent=fit["parent"],
        features=np.array(feature_names),
    )
    card = {
        "sensor": sensor,
        "row_unit": "1 m interval",
        "features": feature_names,
        "minerals": data.minerals,
        "mineral_groups": data.mineral_group,
        "clustering": fit["card"],
        "anomaly": anomaly_card,
        "domains": {
            "method": "penalised optimal partition (change in mean, Hellinger features)",
            "min_length_bins": domains.MIN_LENGTH,
            "count": int(len(domain_table)),
            "median_length_m": float((domain_table.depth_to_m - domain_table.depth_from_m).median())
            if len(domain_table)
            else None,
        },
        "validation": validation_card,
        "data_quality_outliers": quality[quality.support_z.abs() > 3.5]
        .round(3)
        .reset_index()
        .rename(columns={"index": "hole_id"})
        .to_dict("records"),
        "holes": int(data.meta.hole_id.nunique()),
        "intervals": int(len(data)),
    }
    with conn.transaction():
        _store_run(conn, run_id, "interval", sensor, card, path)
        profiles = pd.DataFrame(fit["profiles"])
        holes_per = pd.Series(data.meta.hole_id.to_numpy())
        profiles["n_holes"] = [
            int(
                holes_per[
                    (fit["labels"] if level == "fine" else fit["coarse_labels"]) == cid
                ].nunique()
            )
            for level, cid in zip(profiles.level, profiles.cluster_id, strict=True)
        ]
        profiles["groups"] = [{}] * len(profiles)
        profiles["chemistry"] = [
            cluster_chem.get(int(cid)) if level == "fine" else None
            for level, cid in zip(profiles.level, profiles.cluster_id, strict=True)
        ]
        profiles.insert(0, "run_id", run_id)
        profiles["parent_id"] = profiles.parent_id.astype("Int64")
        copy_frame(
            conn,
            "cluster_profile",
            profiles[
                [
                    "run_id",
                    "level",
                    "cluster_id",
                    "parent_id",
                    "name",
                    "n_intervals",
                    "n_holes",
                    "minerals",
                    "groups",
                    "chemistry",
                ]
            ],
            json_columns=("minerals", "groups", "chemistry"),
        )
        labels = pd.DataFrame(
            {
                "run_id": run_id,
                "feature_set_id": data.meta.feature_set_id,
                "bin": data.meta.bin,
                "hole_id": data.meta.hole_id,
                "cluster_id": fit["labels"],
                "coarse_cluster_id": fit["coarse_labels"],
                "distance": np.round(fit["distance"], 5),
                "domain_id": domain_id,
                "anomaly_score": np.round(score, 4),
                "anomaly_reasons": reasons,
            }
        )
        copy_frame(conn, "interval_label", labels, array_columns=("anomaly_reasons",))
        if len(domain_table):
            domain_table.insert(0, "run_id", run_id)
            copy_frame(conn, "domain", domain_table, json_columns=("minerals",))
    return {
        "run_id": run_id,
        "data": data,
        "fit": fit,
        "quality": quality,
        "groups": groups,
        "group_names": group_names,
        "score": score,
    }


def hole_run(
    conn,
    data_dir: Path,
    sensor: str,
    names: list[str],
    distance: np.ndarray,
    summaries: dict[str, dict],
    card: dict,
    extra: pd.DataFrame | None = None,
    naming: pd.DataFrame | None = None,
) -> str:
    labels, cluster_card, leaves = fingerprints.cluster_holes(distance, SEED)
    if naming is not None:
        table = naming.reindex(names).fillna(0)
        table = table.T.groupby(level=0).sum().T
        table = table.div(table.sum(axis=1).replace(0, 1), axis=0)
        cluster_card["names"] = {
            int(c): clusters.name_profile(table[labels == c].mean(), dominant_share=0.5, parts=3)
            for c in np.unique(labels)
        }
        cluster_card["sizes"] = {int(c): int((labels == c).sum()) for c in np.unique(labels)}
    embedding = fingerprints.embed(distance, SEED)
    outlier = fingerprints.hole_anomaly(distance)
    run_id = _run_id("hole", sensor)
    path = data_dir / "products" / "analysis" / run_id
    path.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path / "distance.npz",
        names=np.array(names),
        distance=distance.astype(np.float32),
        leaves=leaves,
    )
    if extra is not None:
        extra.to_parquet(path / "fingerprints.parquet")
    card = {**card, "hole_clustering": cluster_card, "holes": len(names)}
    with conn.transaction():
        _store_run(conn, run_id, "hole", sensor, card, path)
        profile = pd.DataFrame(
            {
                "run_id": run_id,
                "hole_id": names,
                "cluster_id": labels,
                "x": np.round(embedding[:, 0], 3),
                "y": np.round(embedding[:, 1], 3),
                "anomaly_score": np.round(outlier, 4),
                "anomaly_reasons": [["unlike_other_holes"] if v >= 0.98 else [] for v in outlier],
                "summary": [summaries.get(h, {}) for h in names],
            }
        )
        copy_frame(
            conn,
            "hole_profile",
            profile,
            json_columns=("summary",),
            array_columns=("anomaly_reasons",),
        )
        copy_frame(
            conn,
            "hole_neighbor",
            fingerprints.neighbours(names, distance).assign(run_id=run_id)[
                ["run_id", "hole_id", "rank", "neighbor_id", "distance"]
            ],
        )
    return run_id


def hole_views(conn, data_dir: Path, run: dict, log) -> dict:
    data, fit = run["data"], run["fit"]
    sensor = data.sensor
    share = fingerprints.shares(data.meta.hole_id, fit["labels"], fit["k"])
    counts = data.meta.groupby("hole_id").size()
    share = share[counts.reindex(share.index) >= fingerprints.MIN_INTERVALS]
    names = share.index.tolist()
    mean = fingerprints.mean_abundance(data.meta.hole_id, data.abundance, data.minerals).loc[names]
    js = fingerprints.jensen_shannon(share.to_numpy())
    profiles = {p["cluster_id"]: p["name"] for p in fit["profiles"] if p["level"] == "fine"}
    quality = run["quality"]
    anomaly_by_hole = pd.Series(run["score"]).groupby(data.meta.hole_id.to_numpy()).quantile(0.99)
    summaries = {}
    for hole_id in names:
        top = mean.loc[hole_id].sort_values(ascending=False)
        clusters_top = share.loc[hole_id].sort_values(ascending=False)
        summaries[hole_id] = {
            "intervals": int(counts[hole_id]),
            "top_minerals": {m: round(float(v), 4) for m, v in top[top > 0.01].head(8).items()},
            "top_clusters": {
                profiles[int(c)]: round(float(v), 4)
                for c, v in clusters_top[clusters_top > 0.02].head(6).items()
            },
            "support": round(float(quality.support.get(hole_id, np.nan)), 4),
            "support_z": round(float(quality.support_z.get(hole_id, np.nan)), 2),
            "interval_anomaly_p99": round(float(anomaly_by_hole.get(hole_id, np.nan)), 4),
        }
    fingerprint_table = pd.concat(
        [share.add_prefix("cluster_"), mean.add_prefix("mineral_")], axis=1
    )
    run_id = hole_run(
        conn,
        data_dir,
        sensor,
        names,
        js,
        summaries,
        {
            "sensor": sensor,
            "fingerprint": "share of intervals per fine cluster + mean mineral abundance",
            "distance": "Jensen-Shannon (base 2) on cluster shares",
            "interval_run": run["run_id"],
            "cluster_names": profiles,
        },
        fingerprint_table,
        naming=mean,
    )
    log(f"{sensor}: hole run {run_id} ({len(names)} holes)")
    return {
        "minerals": mean,
        "run_id": run_id,
        "names": names,
        "matrix": js,
        "summaries": summaries,
        "label": f"processed {sensor.upper()} (JS)",
    }


def combined_view(conn, data_dir: Path, views: dict[str, dict], log) -> str | None:
    if len(views) < 2:
        return None
    names = sorted(set().union(*(set(v["names"]) for v in views.values())))
    index = {name: i for i, name in enumerate(names)}
    total = np.zeros((len(names), len(names)))
    count = np.zeros_like(total)
    for view in views.values():
        idx = [index[n] for n in view["names"]]
        total[np.ix_(idx, idx)] += view["matrix"]
        count[np.ix_(idx, idx)] += 1
    distance = np.where(count > 0, total / np.maximum(count, 1), 1.0)
    np.fill_diagonal(distance, 0)
    summaries = {}
    for name in names:
        summaries[name] = {
            s: v["summaries"].get(name) for s, v in views.items() if name in v["summaries"]
        }
    run_id = hole_run(
        conn,
        data_dir,
        "combined",
        names,
        distance,
        summaries,
        {
            "sensor": "combined",
            "distance": "mean of SWIR and TIR JS distances where both exist; "
            "1.0 where a pair shares no sensor",
            "views": {s: v["run_id"] for s, v in views.items()},
        },
        naming=pd.concat([v["minerals"] for v in views.values()], axis=1)
        .T.groupby(level=0)
        .sum()
        .T,
    )
    log(f"combined: hole run {run_id} ({len(names)} holes)")
    return run_id


def raw_views(conn, data_dir: Path, processed: dict[str, dict], log) -> dict:
    products = read_frame(conn, "SELECT hole_id, sensor, product_path FROM raw_product")
    products["product_path"] = [str(resolve_product(p, data_dir)) for p in products.product_path]
    out = {}
    for sensor, processed_sensor in (("nir", "swir"), ("tir", "tir")):
        part = products[products.sensor == sensor]
        if part.hole_id.nunique() < 3:
            log(f"raw {sensor}: {part.hole_id.nunique()} summarised holes; need at least 3")
            continue
        result = rawcluster.analyse(part, sensor, processed.get(processed_sensor), SEED)
        if result is None:
            continue
        summaries = {}
        meta = result["meta"]
        band_columns = [c for c in meta.columns if c.endswith(("_pos", "_depth", "_height"))]
        for hole_id, rows in meta.groupby("hole_id"):
            summaries[hole_id] = {
                "intervals": int(len(rows)),
                "bands_median": {c: _finite(rows[c].median()) for c in band_columns},
                "classes": {
                    int(c): round(float(v), 4)
                    for c, v in result["shares"].loc[hole_id].items()
                    if v > 0.02
                },
            }
        run_id = hole_run(
            conn,
            data_dir,
            f"raw_{sensor}",
            result["names"],
            result["wasserstein"],
            summaries,
            {
                **result["card"],
                "sensor": f"raw_{sensor}",
                "class_profiles": result["class_profiles"],
                "grid_nm": result["grid"].tolist(),
            },
            result["shares"].add_prefix("class_"),
            naming=result["shares"].rename(columns=lambda c: f"class {c}"),
        )
        path = data_dir / "products" / "analysis" / run_id
        meta[
            ["hole_id", "bin", "depth_from_m", "depth_to_m", "spectral_class", "class_distance"]
        ].to_parquet(path / "interval_classes.parquet", index=False)
        out[sensor] = run_id
        log(f"raw {sensor}: hole run {run_id} ({len(result['names'])} holes)")
    return out


def prediction_run(
    conn, data_dir: Path, run: dict, holes: pd.DataFrame, log, survey=None
) -> str | None:
    data = run["data"]
    groups, names = run["groups"], run["group_names"]
    by_hole = pd.DataFrame(groups, columns=names).groupby(data.meta.hole_id.to_numpy()).mean()
    counts = data.meta.groupby("hole_id").size()
    by_hole = by_hole[counts.reindex(by_hole.index) >= fingerprints.MIN_INTERVALS]
    located = holes.reindex(by_hole.index)
    keep = located.lat.notna() & located.lon.notna()
    by_hole, located = by_hole[keep.to_numpy()], located[keep]
    if len(by_hole) < 20:
        log(f"{data.sensor}: too few located holes for prediction CV ({len(by_hole)})")
        return None
    frame = located.reset_index().rename(columns={"index": "hole_id"})
    table, by_distance, summary = prediction.cross_validate(frame, by_hole.to_numpy())
    survey = survey or desurvey.load(conn, holes)
    bands = prediction.band_compositions(data.meta, groups, survey)
    covariate, covariate_table = prediction.covariate_cv(frame, by_hole.to_numpy())
    covariate_table = covariate_table.merge(
        table[["hole_id", "lodo_nearest_km", "region_error"]], on="hole_id"
    )
    covariate["by_distance"] = prediction._by_distance(
        covariate_table, "lodo_nearest_km", "covariate_error"
    )
    covariate["skill_vs_region"] = round(
        float(1 - covariate_table.covariate_error.median() / covariate_table.region_error.median()),
        3,
    )
    covariate["beats_lodo_idw"] = bool(covariate["median_error"] < summary["lodo_median_error"])
    band_rows = [
        {"hole_id": h, "band": b, **dict(zip(names, v.round(5), strict=True))}
        for (h, b), v in bands.items()
    ]
    run_id = _run_id("prediction", data.sensor)
    path = data_dir / "products" / "analysis" / run_id
    path.mkdir(parents=True, exist_ok=True)
    by_hole.assign(lat=located.lat.to_numpy(), lon=located.lon.to_numpy()).to_parquet(
        path / "holes.parquet"
    )
    pd.DataFrame(band_rows).to_parquet(path / "bands.parquet", index=False)
    table.merge(covariate_table[["hole_id", "covariate_error"]], on="hole_id").to_parquet(
        path / "cv.parquet", index=False
    )
    if covariate["beats_lodo_idw"]:
        import joblib

        joblib.dump(
            prediction.fit_covariate_models(frame, by_hole.to_numpy()), path / "regional.joblib"
        )
    card = {
        "sensor": data.sensor,
        "target": "mean mineral-group composition per hole (and per vertical-depth band)",
        "method": f"inverse-distance weighting, k={prediction.K}, power={prediction.POWER}",
        "groups": names,
        "depth_bands_m": prediction.DEPTH_BANDS,
        "summary": summary,
        "by_distance": by_distance,
        "covariate_model": {
            **covariate,
            "method": "gradient boosting on lat/lon, tectonic region and IDW from other districts; "
            "folds hold out whole districts",
            "used_beyond_support": covariate["beats_lodo_idw"],
        },
        "desurvey": {"surveyed_holes": len(survey.surveyed()), "method": "minimum curvature"},
        "interval_run": run["run_id"],
    }
    with conn.transaction():
        _store_run(conn, run_id, "prediction", data.sensor, card, path)
    log(f"{data.sensor}: prediction CV {summary}")
    return run_id


def _finite(value: float) -> float | None:
    return float(value) if value is not None and np.isfinite(value) else None


def virtual_assay_run(conn, data_dir: Path, datasets: dict, holes: pd.DataFrame, log):
    """Fit chemistry-from-mineralogy models on SWIR+TIR together; store predictions."""
    features = {s: (d.meta, *model_features(d)) for s, d in datasets.items()}
    meta, x, columns = virtual_assay.combined_features(features)
    collars = holes.wamex_collar_id.dropna().astype(int).to_dict()
    chem = chemistry.assays(conn, sorted(set(collars.values())))
    joined = chemistry.join_intervals(meta, chem, collars)
    if joined.empty:
        return None, None
    result = virtual_assay.fit(meta, x, joined, SEED)
    if not result["models"]:
        return None, None
    log(
        "virtual assays: "
        + ", ".join(f"{r['analyte']} R2={r['r2_holdout_holes']}" for r in result["skill"])
    )
    predictions = virtual_assay.predict(result["models"], x)
    predictions.insert(0, "bin", meta.bin.to_numpy())
    predictions.insert(0, "hole_id", meta.hole_id.to_numpy())
    run_id = _run_id("virtual_assay", "combined")
    path = data_dir / "products" / "analysis" / run_id
    path.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(path / "predictions.parquet", index=False)
    card = {
        "method": "Histogram gradient boosting on SWIR+TIR Hellinger mineral abundances per "
        "1 m bin; target log10(analyte); skill from GroupKFold with whole holes held out",
        "features": columns,
        "skill": result["skill"],
        "assay_intervals": int(len(joined)),
        "meaning": "Predicted chemistry consistent with the mineralogy; not a measured assay",
    }
    with conn.transaction():
        _store_run(conn, run_id, "virtual_assay", "combined", card, path)
    mismatch = virtual_assay.mismatch_by_bin(meta, joined, result["z"])
    return run_id, mismatch


def run_analysis(
    conn: psycopg.Connection,
    data_dir: Path,
    *,
    mode: str = "retrain",
    promote: bool = True,
    raw: bool = True,
    log: Callable[[str], None] = print,
    progress: Callable[..., None] = lambda **_: None,
) -> dict:
    started = time.perf_counter()
    holes = hole_attributes(conn)
    created: list[str] = []
    views: dict[str, dict] = {}
    survey = desurvey.load(conn, holes)
    log(f"desurvey: {len(survey.surveyed())} holes with surveys (others treated as vertical)")
    progress(stage="loading intervals")
    datasets = {sensor: load_intervals(conn, sensor) for sensor in SENSORS}
    datasets = {s: d for s, d in datasets.items() if len(d) >= 500}
    progress(stage="virtual assays")
    virtual_id, mismatch = virtual_assay_run(conn, data_dir, datasets, holes, log)
    if virtual_id:
        created.append(virtual_id)
    for step, sensor in enumerate(SENSORS):
        progress(stage=f"{sensor} intervals", step=step)
        run = interval_run(
            conn, data_dir, sensor, holes, log, data=datasets.get(sensor), chem_mismatch=mismatch
        )
        if run is None:
            continue
        created.append(run["run_id"])
        progress(stage=f"{sensor} holes", step=step)
        views[sensor] = hole_views(conn, data_dir, run, log)
        created.append(views[sensor]["run_id"])
        progress(stage=f"{sensor} prediction", step=step)
        predicted = prediction_run(conn, data_dir, run, holes, log, survey)
        if predicted:
            created.append(predicted)
        del run
        datasets.pop(sensor, None)
    combined = combined_view(conn, data_dir, views, log)
    if combined:
        created.append(combined)
    if raw:
        progress(stage="raw spectra")
        created.extend(raw_views(conn, data_dir, views, log).values())
    if promote:
        for run_id in created:
            promote_run(conn, run_id)
    return {
        "runs": created,
        "promoted": promote,
        "mode": mode,
        "seconds": round(time.perf_counter() - started, 1),
    }


promote_run = promote


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("C:/HyLoggerData"))
    parser.add_argument("--no-promote", action="store_true")
    parser.add_argument("--no-raw", action="store_true")
    args = parser.parse_args()
    with connect() as conn:
        result = run_analysis(conn, args.data_dir, promote=not args.no_promote, raw=not args.no_raw)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()


def run_robustness(
    conn, data_dir: Path, *, sample_holes: int = 150, log=print, promote=True
) -> dict:
    """District-transfer and interval-width checks for the active interval clustering."""
    from . import robustness

    holes = hole_attributes(conn)
    district = holes.district_id.dropna().astype(int).to_dict()
    created = []
    for sensor in SENSORS:
        active = read_frame(
            conn,
            "SELECT card->'clustering'->>'k_fine' AS k FROM analysis_run "
            "WHERE kind='interval' AND sensor=%s AND status='active'",
            (sensor,),
        )
        data = load_intervals(conn, sensor)
        if active.empty or len(data) < 500:
            continue
        k = int(active.k.iloc[0])
        transfer = robustness.district_transfer(data, k, district, seed=SEED)
        mean_ari, min_ari = transfer["mean_ari"], transfer["min_ari"]
        log(f"{sensor}: district transfer ARI mean {mean_ari} (min {min_ari})")
        rng = np.random.default_rng(SEED)
        candidates = sorted(data.meta.hole_id.unique())
        sample = sorted(rng.choice(candidates, min(sample_holes, len(candidates)), replace=False))
        width = robustness.width_sensitivity(data_dir, sensor, sample, seed=SEED)
        log(f"{sensor}: width sensitivity {width.get('mantel_vs_1m')} K {width.get('k_by_width')}")
        run_id = _run_id("robustness", sensor)
        path = data_dir / "products" / "analysis" / run_id
        path.mkdir(parents=True, exist_ok=True)
        card = {
            "district_transfer": transfer,
            "width_sensitivity": width,
            "meaning": "ARI near 1: withheld districts fall into the same clusters as in the "
            "full fit. Mantel rho near 1: hole similarity does not depend on interval width.",
        }
        with conn.transaction():
            _store_run(conn, run_id, "robustness", sensor, card, path)
        created.append(run_id)
    if promote:
        for run_id in created:
            promote_run(conn, run_id)
    return {"runs": created}

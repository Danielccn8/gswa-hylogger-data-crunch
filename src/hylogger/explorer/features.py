"""Mineral-level interval features from processed TSG logs (SWIR, TIR and VNIR).

Each reading carries up to three TSA/jCLST components (Min1-3 with weights
Wt1-3). Following Sathi's abundance table (scripts/tsas_data_clustering.py,
create_mineral_abundance_table), a reading's weights are normalised to sum to
one and assigned to the named minerals. An interval's mineral abundance is the
mean over its diagnostic readings, so it also sums to one. Readings pass the
shared reading contract (preparation.gate_readings) first.

Abundances are relative TSG interpretation weights, not mineral mass percent.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..preparation import QC_VERSION, gate_readings
from ..util import sha256_file

SENSORS = {"swir": "u_tsas", "tir": "uj_clst", "vnir": "u_tsav"}
FEATURE_VERSION = f"{QC_VERSION}+minerals-v3-support"
NONDIAGNOSTIC = {"", "INVALID", "ASPECTRAL", "NOTAROK", "NOTAROCK", "NULL", "NAN", "NONE"}
NOT_ROCK_GROUPS = {"NOTAROK", "NOTAROCK"}  # marker pen, core tray, wood, vegetation, tape...
REASONS = (
    "admitted",
    "invalid_depth",
    "unknown_mask",
    "masked",
    "not_rock",
    "nondiagnostic",
    "unknown_class",
)


class MissingLogs(ValueError):
    """The dataset has no Min1/Wt1 logs for this sensor."""


def select_dataset(hole_dir: Path) -> Path | None:
    """Pick one dataset revision per hole: core before cuttings, then most recent."""
    candidates = sorted(hole_dir.glob("*/scalar_logs.parquet"))
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    meta: dict[str, dict] = {}
    inventory = hole_dir / "inventory.json"
    if inventory.exists():
        for item in json.loads(inventory.read_text(encoding="utf-8")).get("datasets", []):
            meta[str(item.get("dataset_id"))] = item

    def item(path: Path) -> dict:
        return meta.get(path.parent.name, {})

    newest_first = sorted(
        candidates, key=lambda path: str(item(path).get("modified_date", "")), reverse=True
    )
    return sorted(
        newest_first, key=lambda path: "cutting" in str(item(path).get("dataset_name", "")).lower()
    )[0]


def _label(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def interval_features(
    source: Path, hole_id: str, sensor: str, width_m: float = 1.0
) -> tuple[pd.DataFrame, dict]:
    if sensor not in SENSORS or not np.isfinite(width_m) or width_m <= 0:
        raise ValueError("Expected swir/tir/vnir and a positive interval width")
    suffix = SENSORS[sensor]
    frame = pd.read_parquet(source).reset_index(drop=True)
    if f"min1_{suffix}" not in frame:
        raise MissingLogs(f"{source} lacks min1_{suffix}")
    weights_available = f"wt1_{suffix}" in frame
    if not weights_available:
        # Narrow historical download: treat Min1 as the whole reading (frequency, not mixture).
        frame[f"wt1_{suffix}"] = 1.0
    if "start_depth" not in frame:
        raise ValueError(f"{source} lacks start_depth")
    dataset_id = source.parent.name
    checksum = sha256_file(source)
    set_id = hashlib.sha256(
        f"{checksum}:{hole_id}:{sensor}:{width_m}:{FEATURE_VERSION}".encode()
    ).hexdigest()[:20]
    frame, shared = gate_readings(frame, depth_column="start_depth", retain_qc_rows=True)
    primary = frame.primary_reason.to_numpy(copy=True)

    parts = []
    for position in (1, 2, 3) if weights_available else (1,):
        mineral_col, weight_col = f"min{position}_{suffix}", f"wt{position}_{suffix}"
        group_col = f"grp{position}_{suffix}"
        if mineral_col not in frame or weight_col not in frame:
            continue
        mineral = _label(frame[mineral_col])
        group = (
            _label(frame[group_col]) if group_col in frame else pd.Series(pd.NA, index=frame.index)
        )
        weight = pd.to_numeric(frame[weight_col], errors="coerce")
        parts.append(
            pd.DataFrame(
                {"row": frame.index, "mineral": mineral, "group": group.str.upper(), "w": weight}
            )
        )
    long = pd.concat(parts, ignore_index=True)
    numeric_code = pd.to_numeric(long.mineral, errors="coerce").notna()
    not_rock = long.group.isin(NOT_ROCK_GROUPS)
    valid = (
        long.mineral.notna()
        & ~long.mineral.str.upper().isin(NONDIAGNOSTIC)
        & ~numeric_code
        & ~not_rock
        & np.isfinite(long.w.to_numpy(dtype=float, na_value=np.nan))
        & long.w.gt(0)
    )
    code_rows = set(long.loc[numeric_code, "row"])
    artefact_rows = set(long.loc[not_rock & long.w.gt(0), "row"])
    long = long[valid].copy()
    eligible = primary == "admitted"
    has_mineral = np.zeros(len(frame), dtype=bool)
    has_mineral[long.row.unique()] = True
    unknown_class = np.zeros(len(frame), dtype=bool)
    unknown_class[list(code_rows)] = True
    primary[eligible & ~has_mineral & unknown_class] = "unknown_class"
    artefact = np.zeros(len(frame), dtype=bool)
    artefact[list(artefact_rows)] = True
    primary[(primary == "admitted") & ~has_mineral & artefact] = "not_rock"
    primary[(primary == "admitted") & ~has_mineral] = "nondiagnostic"
    counts = {name: int((primary == name).sum()) for name in REASONS}
    if sum(counts.values()) != len(frame):
        raise ValueError("QC counts do not reconcile")

    admitted = primary == "admitted"
    depth = frame.start_depth.to_numpy(dtype=float)
    in_range = np.isfinite(depth) & (depth >= 0)
    bins = np.full(len(frame), -1)
    bins[in_range] = np.floor(depth[in_range] / width_m).astype(int)
    long = long[admitted[long.row.to_numpy()]].copy()
    long["w"] = long.w / long.groupby("row").w.transform("sum")
    long["bin"] = bins[long.row.to_numpy()]
    long["group"] = long.group.fillna("UNKNOWN")

    work = pd.DataFrame({"bin": bins, "reason": primary, "row": frame.source_row})[in_range]
    totals = work.groupby("bin").size()
    admitted_counts = work[work.reason == "admitted"].groupby("bin").size()
    minerals = long.groupby(["bin", "mineral"]).w.sum()
    groups = long.groupby(["bin", "group"]).w.sum()
    error_col = f"error_{suffix}"
    errors = (
        pd.DataFrame({"bin": bins, "e": pd.to_numeric(frame.get(error_col), errors="coerce")})[
            admitted & in_range
        ]
        .groupby("bin")
        .e.mean()
        if error_col in frame
        else pd.Series(dtype=float)
    )
    rows_by_bin = work.groupby("bin").row.apply(list)
    reason_counts = work.groupby(["bin", "reason"]).size().unstack(fill_value=0)

    rows = []
    for bin_id, total in totals.items():
        n_admitted = int(admitted_counts.get(bin_id, 0))
        mix = (minerals.loc[bin_id] / n_admitted).round(5).to_dict() if n_admitted else {}
        grp = (groups.loc[bin_id] / n_admitted).round(5).to_dict() if n_admitted else {}
        start = round(float(bin_id * width_m), 6)
        rows.append(
            {
                "feature_set_id": set_id,
                "bin": int(bin_id),
                "hole_id": hole_id,
                "sensor": sensor,
                "depth_from_m": start,
                "depth_to_m": round(start + width_m, 6),
                "n_total": int(total),
                "n_admitted": n_admitted,
                "diagnostic_fraction": n_admitted / int(total),
                "representation": "component_weight_mixture"
                if weights_available
                else "primary_mineral_frequency",
                "mask_status": "available"
                if shared["mask_status"] == "available"
                else "unavailable_in_processed_log",
                "minerals": {str(k): float(v) for k, v in mix.items()},
                "groups": {str(k): float(v) for k, v in grp.items()},
                "dominant_mineral": max(mix, key=mix.get) if mix else None,
                "dominant_group": max(grp, key=grp.get) if grp else None,
                "error_mean": float(errors.get(bin_id)) if bin_id in errors.index else None,
                "reason_counts": {
                    reason: int(reason_counts.loc[bin_id].get(reason, 0)) for reason in REASONS
                },
                "source_rows": [int(row) for row in rows_by_bin.loc[bin_id]],
            }
        )
    table = pd.DataFrame(rows)
    report = {
        "feature_set_id": set_id,
        "hole_id": hole_id,
        "dataset_id": dataset_id,
        "sensor": sensor,
        "source_sha256": checksum,
        "width_m": width_m,
        "qc_version": FEATURE_VERSION,
        "mask_status": "available"
        if shared["mask_status"] == "available"
        else "unavailable_in_processed_log",
        "rows_received": len(frame),
        "primary_reasons": counts,
        "intervals": len(table),
        "intervals_with_minerals": int((table.n_admitted > 0).sum()) if len(table) else 0,
        "weights_available": weights_available,
        "representation": "component_weight_mixture"
        if weights_available
        else "primary_mineral_frequency",
        "error_policy": (
            "Descriptive fit-error mean; no calibrated mineral-specific accuracy threshold applied"
        ),
        "minerals_seen": sorted(long.mineral.unique().tolist()),
        "mineral_groups": (long.drop_duplicates("mineral").set_index("mineral").group.to_dict()),
        "meaning": (
            "Mean normalised TSG component weight per diagnostic reading; not mass percent"
            if weights_available
            else "Primary-mineral frequency among diagnostic readings; "
            "not a component mixture or mass percent"
        ),
    }
    return table, report


def write_product(table: pd.DataFrame, report: dict, root: Path) -> Path:
    target = root / report["feature_set_id"]
    target.mkdir(parents=True, exist_ok=True)
    out = table.copy()
    for column in ("minerals", "groups", "reason_counts"):
        out[column] = out[column].map(json.dumps)
    out.to_parquet(target / "intervals.parquet", index=False)
    (target / "qc.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target


def read_product(path: Path) -> pd.DataFrame:
    table = pd.read_parquet(path / "intervals.parquet")
    for column in ("minerals", "groups", "reason_counts"):
        table[column] = table[column].map(json.loads)
    return table

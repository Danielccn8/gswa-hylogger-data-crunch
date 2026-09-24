"""Versioned, interval-level TSG products. No source file is modified here."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .util import sha256_file

REGIONS = {"swir": "u_tsas", "tir": "uj_clst"}
INVALID = {"", "INVALID", "ASPECTRAL", "NOTAROK", "NOTAROCK", "NULL", "NAN", "NONE"}


def interval_set(
    source: Path, hole_id: str, region: str, width_m: float = 1.0
) -> tuple[pd.DataFrame, dict]:
    """Create interval features and a reconciled QC report from one dataset revision."""
    if region not in REGIONS or width_m <= 0:
        raise ValueError("Expected swir/tir and a positive interval width")
    frame = pd.read_parquet(source).reset_index(drop=True)
    suffix = REGIONS[region]
    label_col = f"grp1_{suffix}"
    if label_col not in frame or "start_depth" not in frame:
        raise ValueError(f"{source} lacks {label_col} or start_depth")
    dataset_id = source.parent.name
    checksum = sha256_file(source)
    set_id = hashlib.sha256(f"{checksum}:{hole_id}:{region}:{width_m}:v1".encode()).hexdigest()[:20]
    depth = pd.to_numeric(frame.start_depth, errors="coerce")
    mask_col = next((name for name in ("final_mask", "finalmask", "mask") if name in frame), None)
    mask = pd.to_numeric(frame[mask_col], errors="coerce") if mask_col else None
    labels = frame[label_col].astype("string").str.strip().str.upper()
    primary = np.full(len(frame), "admitted", dtype=object)
    primary[~np.isfinite(depth.to_numpy()) | (depth.to_numpy() < 0)] = "invalid_depth"
    if mask is not None:
        eligible = primary == "admitted"
        primary[eligible & mask.isna().to_numpy()] = "unknown_mask"
        primary[eligible & mask.notna().to_numpy() & mask.ne(1).to_numpy()] = "non_rock"
    eligible = primary == "admitted"
    nondiagnostic = labels.isna() | labels.isin(INVALID)
    primary[eligible & nondiagnostic.to_numpy()] = "nondiagnostic"
    counts = {
        name: int((primary == name).sum())
        for name in ("admitted", "invalid_depth", "unknown_mask", "non_rock", "nondiagnostic")
    }
    assert sum(counts.values()) == len(frame)
    work = pd.DataFrame(
        {
            "source_row": np.arange(len(frame)),
            "depth_m": depth,
            "label": labels,
            "primary_reason": primary,
        }
    )
    # Keep valid-depth QC rows in the interval denominator, including unknown
    # mask and nondiagnostic readings. This makes missing support visible.
    work = work[np.isfinite(work.depth_m) & (work.depth_m >= 0)].copy()
    work["bin"] = np.floor(work.depth_m / width_m).astype(int)
    rows = []
    for bin_id, part in work.groupby("bin", sort=True):
        accepted = part[part.primary_reason == "admitted"]
        proportions = accepted.label.value_counts(normalize=True).sort_index().to_dict()
        start = round(float(bin_id * width_m), 6)
        rows.append(
            {
                "interval_id": hashlib.sha256(f"{set_id}:{bin_id}".encode()).hexdigest()[:24],
                "interval_set_id": set_id,
                "hole_id": hole_id,
                "dataset_id": dataset_id,
                "region": region,
                "depth_from_m": start,
                "depth_to_m": round(start + width_m, 6),
                "n_total": int(len(part)),
                "n_admitted": int(len(accepted)),
                "reason_counts": {
                    reason: int((part.primary_reason == reason).sum())
                    for reason in ("admitted", "unknown_mask", "non_rock", "nondiagnostic")
                },
                "source_rows": part.source_row.astype(int).tolist(),
                "source_depth_min_m": float(part.depth_m.min()),
                "source_depth_max_m": float(part.depth_m.max()),
                "features": {str(k): float(v) for k, v in proportions.items()},
                "dominant_group": next(
                    iter(sorted(proportions, key=proportions.get, reverse=True)), None
                ),
            }
        )
    report = {
        "interval_set_id": set_id,
        "hole_id": hole_id,
        "dataset_id": dataset_id,
        "source_path": str(Path("downloads") / "processed" / hole_id / dataset_id / source.name),
        "source_sha256": checksum,
        "region": region,
        "width_m": width_m,
        "mask_status": "available" if mask_col else "unavailable_in_processed_log",
        "rows_received": len(frame),
        "primary_reasons": counts,
        "intervals": len(rows),
        "intervals_with_features": sum(bool(r["features"]) for r in rows),
        "duplicate_depth_rows_retained": int(depth.duplicated(keep=False).sum()),
        "meaning": "TSG group frequency among admitted readings; not rock mass percentage",
    }
    return pd.DataFrame(rows), report


def write_product(table: pd.DataFrame, report: dict, root: Path) -> Path:
    """Publish the two files under their content-derived set ID."""
    target = root / report["interval_set_id"]
    target.mkdir(parents=True, exist_ok=True)
    table.to_parquet(target / "intervals.parquet", index=False)
    (target / "qc.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target

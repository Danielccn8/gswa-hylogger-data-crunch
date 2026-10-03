"""Precomputed summaries for the read-only analytical dashboard."""

from __future__ import annotations

import numpy as np
import pandas as pd


def summarize_set(table: pd.DataFrame, qc: dict) -> dict:
    populated = table[table.n_admitted > 0] if len(table) else table
    keys = (
        sorted(
            {key for item in populated.features for key, value in item.items() if value is not None}
        )
        if len(populated)
        else []
    )
    frequencies = {
        key: float(np.mean([item.get(key) or 0.0 for item in populated.features])) for key in keys
    }
    return {
        "hole_id": qc["hole_id"],
        "region": qc["region"],
        "interval_set_id": qc["interval_set_id"],
        "dataset_id": qc["dataset_id"],
        "intervals": len(table),
        "diagnostic_intervals": len(populated),
        "readings": int(table.n_total.sum()) if len(table) else 0,
        "diagnostic_readings": int(table.n_admitted.sum()) if len(table) else 0,
        "depth_from_m": float(table.depth_from_m.min()) if len(table) else None,
        "depth_to_m": float(table.depth_to_m.max()) if len(table) else None,
        "group_frequencies": frequencies,
        "dominant_group": max(frequencies, key=frequencies.get) if frequencies else None,
        "mask_status": qc["mask_status"],
        "qc_version": qc.get("qc_version", "legacy-v1"),
        "width_m": qc["width_m"],
        "source_sha256": qc["source_sha256"],
        "primary_reasons": qc["primary_reasons"],
        "rows_received": qc["rows_received"],
        "weighting": "equal weight per diagnostic interval within each hole",
    }

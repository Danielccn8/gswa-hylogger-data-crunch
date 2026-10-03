"""Shared reading contract: preserve identity, reconcile QC, keep measurements paired."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .errors import HyLoggerError
from .quality import MASK_COLUMNS, QualityPolicy, apply_quality_policy

QC_VERSION = "reading-contract-v2"


def valid_depth(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return pd.Series(np.isfinite(numeric.to_numpy(dtype=float)), index=values.index) & numeric.ge(0)


def wavelength_grid(spectra: np.ndarray, wavelengths: np.ndarray) -> None:
    if (
        spectra.ndim != 2
        or wavelengths.ndim != 1
        or not len(wavelengths)
        or spectra.shape[1] != len(wavelengths)
        or not np.isfinite(wavelengths).all()
        or not (wavelengths > 0).all()
        or not (np.diff(wavelengths) > 0).all()
    ):
        raise HyLoggerError(
            "Wavelength grid must match spectra and be finite, positive and increasing."
        )


def gate_readings(
    frame: pd.DataFrame,
    *,
    depth_column: str,
    spectra: np.ndarray | None = None,
    policy: QualityPolicy | None = None,
    require_mask: bool = False,
    retain_qc_rows: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """One primary reason per input row; raw arrays remain indexed by source_row.

    Missing processed masks are a documented limitation. Primary spectral work
    requires a mask. Callers may explicitly opt into a labelled sensitivity run.
    """
    policy = policy or QualityPolicy()
    frame = frame.copy().reset_index(drop=True)
    if depth_column not in frame:
        raise HyLoggerError(f"Missing depth column: {depth_column}")
    if "source_row" not in frame:
        frame["source_row"] = np.arange(len(frame))
    source_rows = pd.to_numeric(frame.source_row, errors="coerce")
    if source_rows.isna().any() or (source_rows < 0).any() or source_rows.duplicated().any():
        raise HyLoggerError("source_row must be a unique, non-negative original row index")
    if not source_rows.eq(np.floor(source_rows)).all():
        raise HyLoggerError("source_row must contain integers")
    frame["source_row"] = source_rows.astype(int)
    frame[depth_column] = pd.to_numeric(frame[depth_column], errors="coerce")
    reasons = np.full(len(frame), "admitted", dtype=object)
    reasons[~valid_depth(frame[depth_column]).to_numpy()] = "invalid_depth"
    if spectra is not None:
        if spectra.ndim != 2 or (len(frame) and frame.source_row.max() >= len(spectra)):
            raise HyLoggerError("Metadata source rows do not match the spectral array")
        finite = np.empty(len(frame), dtype=bool)
        indices = frame.source_row.to_numpy()
        for start in range(0, len(frame), 8192):
            selected = spectra[indices[start : start + 8192]]
            finite[start : start + len(selected)] = np.isfinite(selected).all(axis=1) & (
                selected > -1e38
            ).all(axis=1)
        reasons[(reasons == "admitted") & ~finite] = "invalid_spectrum"
    mask_column = next((name for name in MASK_COLUMNS if name in frame), None)
    mask_status = "available" if mask_column else "unavailable"
    if policy.apply_final_mask:
        eligible = reasons == "admitted"
        if mask_column:
            mask = pd.to_numeric(frame[mask_column], errors="coerce")
            known = np.isfinite(mask.to_numpy(dtype=float)) & mask.ge(0).to_numpy()
            reasons[eligible & ~known] = "unknown_mask"
            reasons[eligible & known & mask.ne(1).to_numpy()] = "masked"
        elif require_mask:
            reasons[eligible] = "unknown_mask"
    else:
        mask_status += "_not_applied"
    counts = {
        name: int((reasons == name).sum())
        for name in ("admitted", "invalid_depth", "invalid_spectrum", "unknown_mask", "masked")
    }
    result = frame.loc[reasons == "admitted"]
    result, quality = apply_quality_policy(result, policy)
    result = result.sort_values(depth_column, kind="stable").reset_index(drop=True)
    report = {
        "qc_version": QC_VERSION,
        "rows_received": len(frame),
        "rows_admitted": len(result),
        "primary_reasons": counts,
        "mask_status": mask_status,
        "eligibility": "mask_verified"
        if mask_column and policy.apply_final_mask
        else "mask_unverified",
        "requires_mask": require_mask,
        "duplicate_depth_rows_retained": int(result[depth_column].duplicated(keep=False).sum()),
        "quality_policy": quality,
    }
    if sum(counts.values()) != len(frame) or counts["admitted"] != len(result):
        raise HyLoggerError("QC counts do not reconcile")
    if retain_qc_rows:
        frame["primary_reason"] = reasons
        return frame, report
    return result, report

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .errors import HyLoggerError
from .manifest import Manifest
from .raw import find_package_dir
from .util import atomic_write_frame, atomic_write_json, unique_snake_columns, utc_now


def prepare_hole(config: AppConfig, hole_id: str) -> dict[str, Any]:
    hole_id = hole_id.upper()
    config.ensure_layout()
    package_dir = find_package_dir(config, hole_id)
    if package_dir:
        result = _prepare_raw_package(config, hole_id, package_dir)
    else:
        result = _prepare_processed(config, hole_id)

    Manifest(config.manifest_path).record(
        artifact_key=f"prepared:{hole_id}",
        hole_id=hole_id,
        kind="prepared",
        status="complete",
        local_path=result["sample_paths"][0] if result.get("sample_paths") else None,
        completed_at=utc_now(),
        metadata=result,
    )
    return result


def _prepare_raw_package(config: AppConfig, hole_id: str, package_dir: Path) -> dict[str, Any]:
    from pytsg import parse_tsg

    package = parse_tsg.read_package(package_dir, read_cras_file=False)
    sample_paths: list[str] = []
    frames: dict[str, pd.DataFrame] = {}
    spectra_payload: dict[str, np.ndarray] = {}
    qc: dict[str, Any] = {"hole_id": hole_id, "source": "raw", "sections": {}}

    for region in ("nir", "tir", "mir"):
        section = getattr(package, region, None)
        if section is None or not hasattr(section, "spectra"):
            continue
        frame = _section_frame(section, hole_id=hole_id, region=region.upper())
        frame = _clean_and_sort(frame)
        path = config.data_dir / "prepared" / "samples" / f"{hole_id}_{region}.parquet"
        atomic_write_frame(frame, path)
        sample_paths.append(str(path))
        frames[region] = frame
        spectra_payload[f"{region}_spectra"] = np.asarray(section.spectra)
        spectra_payload[f"{region}_wavelength"] = np.asarray(section.wavelength)
        qc["sections"][region] = _frame_qc(frame, spectra_shape=list(section.spectra.shape))

    if not frames:
        raise HyLoggerError(f"pytsg found no spectral sections in {package_dir}")

    spectra_path = config.data_dir / "prepared" / "spectra" / f"{hole_id}.npz"
    spectra_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = spectra_path.with_name(f".{spectra_path.stem}.tmp.npz")
    np.savez_compressed(temporary, **spectra_payload)
    temporary.replace(spectra_path)

    features = _feature_row(hole_id, frames)
    feature_path = config.data_dir / "prepared" / "features" / f"{hole_id}.parquet"
    atomic_write_frame(pd.DataFrame([features]), feature_path)
    qc_path = config.data_dir / "prepared" / "samples" / f"{hole_id}_qc.json"
    atomic_write_json(qc_path, qc)
    return {
        "hole_id": hole_id,
        "source": "raw",
        "package_dir": str(package_dir),
        "sample_paths": sample_paths,
        "spectra_path": str(spectra_path),
        "feature_path": str(feature_path),
        "qc_path": str(qc_path),
    }


def _prepare_processed(config: AppConfig, hole_id: str) -> dict[str, Any]:
    root = config.data_dir / "downloads" / "processed" / hole_id
    paths = sorted(root.glob("*/scalar_logs.parquet"))
    if not paths:
        raise HyLoggerError(
            f"No raw package or processed logs are available for {hole_id}. "
            f"Run: hylogger processed fetch {hole_id}"
        )
    frame = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    frame = _clean_and_sort(frame)
    sample_path = config.data_dir / "prepared" / "samples" / f"{hole_id}_processed.parquet"
    atomic_write_frame(frame, sample_path)
    features = _feature_row(hole_id, {"processed": frame})
    feature_path = config.data_dir / "prepared" / "features" / f"{hole_id}.parquet"
    atomic_write_frame(pd.DataFrame([features]), feature_path)
    qc = {
        "hole_id": hole_id,
        "source": "processed",
        "sections": {"processed": _frame_qc(frame)},
    }
    qc_path = config.data_dir / "prepared" / "samples" / f"{hole_id}_qc.json"
    atomic_write_json(qc_path, qc)
    return {
        "hole_id": hole_id,
        "source": "processed",
        "sample_paths": [str(sample_path)],
        "feature_path": str(feature_path),
        "qc_path": str(qc_path),
    }


def combine_feature_matrix(config: AppConfig, holes: list[str], name: str) -> Path:
    frames = []
    missing = []
    for hole_id in holes:
        path = config.data_dir / "prepared" / "features" / f"{hole_id.upper()}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
        else:
            missing.append(hole_id)
    if not frames:
        raise HyLoggerError("No prepared feature files were found for the selection.")
    matrix = pd.concat(frames, ignore_index=True).fillna(0)
    output = config.data_dir / "prepared" / "features" / f"selection_{name}.parquet"
    atomic_write_frame(matrix, output)
    metadata = {
        "selection": name,
        "holes_requested": len(holes),
        "holes_in_matrix": len(matrix),
        "missing": missing,
        "created_at": utc_now(),
    }
    atomic_write_json(output.with_suffix(".json"), metadata)
    return output


def _section_frame(section: object, *, hole_id: str, region: str) -> pd.DataFrame:
    headers = section.sampleheaders.copy().reset_index(drop=True)
    headers.columns = unique_snake_columns(headers.columns)
    scalars = section.scalars.copy().reset_index(drop=True)
    scalars.columns = unique_snake_columns(scalars.columns)
    overlap = set(headers.columns) & set(scalars.columns)
    if overlap:
        scalars = scalars.rename(columns={column: f"scalar_{column}" for column in overlap})
    frame = pd.concat([headers, scalars], axis=1)
    # HyLogger packages often already contain HoleID. Make the canonical
    # provenance fields authoritative and move them to the front.
    frame["spectral_region"] = region
    frame["hole_id"] = hole_id
    spectral_region = frame.pop("spectral_region")
    canonical_hole_id = frame.pop("hole_id")
    frame.insert(0, "spectral_region", spectral_region)
    frame.insert(0, "hole_id", canonical_hole_id)
    if "d" in frame.columns:
        frame = frame.rename(columns={"d": "depth_m"})
    elif "start_depth" in frame.columns:
        frame = frame.rename(columns={"start_depth": "depth_m"})
    # Some packages contain both the sample-header depth (`D`) and a scalar
    # called `Depth_m`. Keep both with deterministic, unique names.
    frame.columns = unique_snake_columns(frame.columns)
    return frame


def _clean_and_sort(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.replace({"": np.nan, "null": np.nan, "NULL": np.nan})
    for column in frame.select_dtypes(include=["number"]).columns:
        frame[column] = frame[column].where(frame[column] > -1e38)
    depth = _depth_column(frame)
    if depth:
        frame[depth] = pd.to_numeric(frame[depth], errors="coerce")
        frame = frame.sort_values([depth], kind="stable", na_position="last").reset_index(drop=True)
    return frame


def _frame_qc(frame: pd.DataFrame, spectra_shape: list[int] | None = None) -> dict[str, Any]:
    depth = _depth_column(frame)
    result: dict[str, Any] = {
        "rows": len(frame),
        "columns": len(frame.columns),
    }
    if spectra_shape:
        result["spectra_shape"] = spectra_shape
    if depth:
        values = pd.to_numeric(frame[depth], errors="coerce")
        result.update(
            {
                "depth_column": depth,
                "depth_min_m": float(values.min()) if values.notna().any() else None,
                "depth_max_m": float(values.max()) if values.notna().any() else None,
                "duplicate_depths": int(values.duplicated().sum()),
            }
        )
    mask = next((column for column in frame.columns if column in {"final_mask", "mask"}), None)
    if mask:
        numeric = pd.to_numeric(frame[mask], errors="coerce")
        result["kept_pct"] = round(float((numeric == 1).mean() * 100), 2)
    return result


def _feature_row(hole_id: str, frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    output: dict[str, Any] = {"hole_id": hole_id}
    for region, frame in frames.items():
        category_columns = [
            column for column in frame.columns if re.match(r"^(grp|group|min|mineral)_?1", column)
        ]
        for column in category_columns:
            values = frame[column].dropna().astype(str).str.strip()
            values = values[~values.str.upper().isin({"", "INVALID", "ASPECTRAL", "NOTAROK"})]
            if values.empty:
                continue
            proportions = values.value_counts(normalize=True)
            for category, proportion in proportions.items():
                safe = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
                output[f"{region}__{column}__{safe}"] = float(proportion)
    output["prepared_at"] = utc_now()
    return output


def _depth_column(frame: pd.DataFrame) -> str | None:
    for candidate in ("depth_m", "start_depth", "startdepth", "depth"):
        if candidate in frame.columns:
            return candidate
    return None

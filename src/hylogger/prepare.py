from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .errors import HyLoggerError
from .manifest import Manifest
from .preparation import gate_readings, wavelength_grid
from .raw import find_package_dir
from .util import (
    atomic_write_frame,
    atomic_write_json,
    sha256_file,
    unique_snake_columns,
    utc_now,
)


def prepare_hole(config: AppConfig, hole_id: str, *, source: str = "auto") -> dict[str, Any]:
    """Prepare exactly the requested source; auto retains the legacy raw-first rule."""
    if source not in {"auto", "processed", "raw"}:
        raise HyLoggerError("source must be auto, processed, or raw.")
    hole_id = hole_id.upper()
    config.ensure_layout()
    package_dir = find_package_dir(config, hole_id) if source != "processed" else None
    if source == "raw" and package_dir is None:
        raise HyLoggerError(
            f"No extracted raw package is available for {hole_id}. "
            f"Run: hylogger raw fetch {hole_id} --components spectra"
        )
    if package_dir:
        result = _prepare_raw_package(config, hole_id, package_dir)
    else:
        result = _prepare_processed(config, hole_id)

    Manifest(config.manifest_path).record(
        artifact_key=f"prepared:{result['source']}:{hole_id}",
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
        spectra = np.asarray(section.spectra)
        if len(spectra) != len(frame):
            raise HyLoggerError(f"{hole_id} {region}: metadata and spectra row counts differ")
        wavelength_grid(spectra, np.asarray(section.wavelength, dtype=float))
        frame, quality = gate_readings(
            frame, depth_column="depth_m", spectra=spectra, require_mask=True
        )
        selected_spectra = spectra[frame["source_row"].to_numpy(dtype=int)]
        path = config.data_dir / "prepared" / "samples" / f"{hole_id}_{region}.parquet"
        atomic_write_frame(frame, path)
        sample_paths.append(str(path))
        frames[region] = frame
        spectra_payload[f"{region}_spectra"] = selected_spectra
        spectra_payload[f"{region}_wavelength"] = np.asarray(section.wavelength)
        qc["sections"][region] = _frame_qc(frame, spectra_shape=list(selected_spectra.shape))
        qc["sections"][region]["quality_policy"] = quality
        qc["sections"][region]["spectral_invalid_rows"] = quality["primary_reasons"][
            "invalid_spectrum"
        ]

    if not frames:
        raise HyLoggerError(f"pytsg found no spectral sections in {package_dir}")

    spectra_path = config.data_dir / "prepared" / "spectra" / f"{hole_id}.npz"
    spectra_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = spectra_path.with_name(f".{spectra_path.stem}.tmp.npz")
    np.savez_compressed(temporary, **spectra_payload)
    temporary.replace(spectra_path)

    features = _feature_row(hole_id, frames)
    feature_path = feature_file(config, hole_id, source="raw")
    atomic_write_frame(pd.DataFrame([features]), feature_path)
    qc_path = config.data_dir / "prepared" / "samples" / f"{hole_id}_raw_qc.json"
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
            f"No downloaded processed logs are available for {hole_id}. "
            f"Run: hylogger processed fetch {hole_id}"
        )
    # Different dataset versions can overlap in depth. Do not silently count
    # the same hole twice; a dataset-selection policy is needed before pooling.
    if len(paths) != 1:
        raise HyLoggerError(
            f"{hole_id} has {len(paths)} downloaded processed datasets. "
            "Choose a single reviewed dataset before preparing a hole-level baseline; "
            "automatic concatenation could double-count overlapping depths."
        )
    frame = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    frame, quality = gate_readings(frame, depth_column="start_depth")
    sample_path = config.data_dir / "prepared" / "samples" / f"{hole_id}_processed.parquet"
    atomic_write_frame(frame, sample_path)
    features = _feature_row(hole_id, {"processed": frame})
    feature_path = feature_file(config, hole_id, source="processed")
    atomic_write_frame(pd.DataFrame([features]), feature_path)
    qc = {
        "hole_id": hole_id,
        "source": "processed",
        "input_paths": [str(path) for path in paths],
        "dataset_ids": [path.parent.name for path in paths],
        "category_logs": _category_qc(frame),
        "sections": {"processed": {**_frame_qc(frame), "quality_policy": quality}},
    }
    qc_path = config.data_dir / "prepared" / "samples" / f"{hole_id}_processed_qc.json"
    atomic_write_json(qc_path, qc)
    return {
        "hole_id": hole_id,
        "source": "processed",
        "sample_paths": [str(sample_path)],
        "feature_path": str(feature_path),
        "qc_path": str(qc_path),
    }


def feature_file(config: AppConfig, name: str, *, source: str) -> Path:
    if source not in {"processed", "raw"}:
        raise HyLoggerError("Choose an explicit processed or raw feature source.")
    return config.data_dir / "prepared" / "features" / source / f"{name}.parquet"


def combine_feature_matrix(config: AppConfig, holes: list[str], name: str, *, source: str) -> Path:
    holes = [hole.upper() for hole in holes]
    if not holes or len(set(holes)) != len(holes):
        raise HyLoggerError("A feature selection must contain distinct hole IDs and not be empty.")
    frames = []
    missing = []
    input_hashes = {}
    for hole_id in holes:
        path = feature_file(config, hole_id.upper(), source=source)
        if path.exists():
            frame = pd.read_parquet(path)
            if (
                len(frame) != 1
                or frame["hole_id"].tolist() != [hole_id.upper()]
                or "feature_source" not in frame
                or not frame["feature_source"].eq(source).all()
            ):
                raise HyLoggerError(
                    f"Inconsistent source or hole provenance in {path}. Re-prepare."
                )
            frames.append(frame)
            input_hashes[hole_id.upper()] = sha256_file(path)
        else:
            missing.append(hole_id)
    if missing:
        raise HyLoggerError(f"Missing {source} features for: {', '.join(missing)}. Re-prepare.")
    # NaN remains unknown here. The baseline may fill an unobserved category
    # with zero only after checking that its parent log has valid observations.
    matrix = pd.concat(frames, ignore_index=True)
    output = feature_file(config, f"selection_{name}", source=source)
    atomic_write_frame(matrix, output)
    metadata = {
        "selection": name,
        "source": source,
        "hole_ids": [hole.upper() for hole in holes],
        "input_sha256": input_hashes,
        "matrix_sha256": sha256_file(output),
        "holes_requested": len(holes),
        "holes_in_matrix": len(matrix),
        "missing": missing,
        "created_at": utc_now(),
    }
    atomic_write_json(output.with_suffix(".json"), metadata)
    atomic_write_json(output.with_suffix(".status.json"), {"status": "complete"})
    return output


def _section_frame(section: object, *, hole_id: str, region: str) -> pd.DataFrame:
    headers = section.sampleheaders.copy().reset_index(drop=True)
    headers.columns = unique_snake_columns(headers.columns)
    scalars = section.scalars.copy().reset_index(drop=True)
    if len(headers) != len(scalars):
        raise HyLoggerError("Sample headers and scalar rows differ")
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
    frame["source_row"] = np.arange(len(frame))
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
    output: dict[str, Any] = {
        "hole_id": hole_id,
        "feature_source": "processed" if set(frames) == {"processed"} else "raw",
    }
    for region, frame in frames.items():
        for column, counts in _category_qc(frame).items():
            for key, count in counts.items():
                output[f"qc__{region}__{column}__{key}"] = count
            values = _valid_categories(frame[column])
            if values.empty:
                continue
            proportions = values.value_counts(normalize=True)
            for category, proportion in proportions.items():
                safe = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
                output[f"{region}__{column}__{safe}"] = float(proportion)
    output["prepared_at"] = utc_now()
    return output


def _valid_categories(series: pd.Series) -> pd.Series:
    values = series.dropna().astype(str).str.strip().str.upper()
    invalid = values.str.replace(r"[^A-Z0-9]", "", regex=True).isin(
        {"", "INVALID", "ASPECTRAL", "NOTAROK", "NOTAROCK", "NULL", "NAN", "NONE"}
    )
    invalid |= pd.to_numeric(values, errors="coerce").notna()
    # Match the output names before counting, so spelling/punctuation variants
    # cannot overwrite each other's proportions.
    return values[~invalid].str.lower().str.replace(r"[^a-z0-9]+", "_", regex=True).str.strip("_")


def _category_qc(frame: pd.DataFrame) -> dict[str, dict[str, int]]:
    result = {}
    for column in frame.columns:
        if not re.match(r"^(grp|group|min|mineral)_?1(?:_|$)", column):
            continue
        missing = int(frame[column].isna().sum())
        valid = len(_valid_categories(frame[column]))
        result[column] = {
            "total_rows": len(frame),
            "valid_rows": valid,
            "missing_rows": missing,
            "invalid_rows": len(frame) - missing - valid,
        }
    return result


def _depth_column(frame: pd.DataFrame) -> str | None:
    for candidate in ("depth_m", "start_depth", "startdepth", "depth"):
        if candidate in frame.columns:
            return candidate
    return None

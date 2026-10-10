"""Compact per-hole summaries of raw HyLogger spectra for whole-hole analysis.

For each sensor the package section is read once, readings pass the shared
contract with Final Mask required, and 1 m median spectra are formed (as in
intervals.build_intervals). The product keeps those medians on a coarser grid
(float16) plus absorption-feature parameters, so the source package can be
discarded afterwards when the user asks for summary mode.

Band parameters use a local upper-hull continuum inside each window: position
is the quadratic-refined minimum of the continuum-removed spectrum and depth
is one minus that minimum. TIR indices are experimental reflectance-peak
positions and heights; they are descriptive, not calibrated mineral amounts.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..errors import HyLoggerError
from ..intervals import _reading_frame, _upper_hull_indices, read_package_metadata
from ..preparation import QC_VERSION, gate_readings, wavelength_grid
from . import tsa_versions

RAW_VERSION = f"{QC_VERSION}+raw-summary-v1"
SECTIONS = {"nir": ("*_tsg.tsg", "*_tsg.bip"), "tir": ("*_tsg_tir.tsg", "*_tsg_tir.bip")}
GRIDS = {"nir": np.arange(450.0, 2451.0, 10.0), "tir": np.arange(6000.0, 14501.0, 50.0)}
# name: (low nm, high nm, kind) with kind "absorption" (minimum) or "peak" (maximum)
BANDS = {
    "nir": {
        "fe3_900": (760, 1250, "absorption"),
        "oh_1400": (1340, 1480, "absorption"),
        "h2o_1900": (1850, 2060, "absorption"),
        "aloh_2200": (2120, 2245, "absorption"),
        "feoh_2250": (2230, 2280, "absorption"),
        "mgoh_2330": (2290, 2370, "absorption"),
    },
    "tir": {
        "silicate_peak": (7700, 11500, "peak"),
        "quartz_8600": (8000, 9400, "peak"),
        "carbonate_11300": (11000, 11600, "peak"),
    },
}


def read_section(tsg_file: Path, bip_file: Path, sensor: str):
    """pytsg.read_tsg_bip_pair with class-coded scalars mapped safely.

    pytsg maps class codes to names only for class tables numbered above 0, so a
    table numbered 0 (often the system TSA minerals) comes back as numbers, and a
    code missing from its table raises KeyError. Here every class table is used
    and unknown codes become "class code N".
    """
    from pytsg import parse_tsg

    text = parse_tsg._read_tsg_file(tsg_file)
    info = parse_tsg._parse_tsg(text, parse_tsg._find_header_sections(text))
    data = parse_tsg._read_bip(bip_file, info["coordinates"])
    wavelength = parse_tsg._calculate_wavelengths(info["wavelength specs"], info["coordinates"])
    nodata = np.finfo("float32").min
    columns = []
    for band in info["band headers"]:
        values = data[1, :, band.band]
        table = info["class"].get(band.class_number) if band.flag == 2 else None
        if table is not None:
            missing = np.isclose(values, nodata) | ~np.isfinite(values)
            codes = pd.Series(np.where(missing, -1, values).astype(int))
            names = codes.map(table.classes)
            unknown = names.isna() & codes.ge(0)
            names[unknown] = "class code " + codes[unknown].astype(str)
            values = names.where(codes.ge(0), "").to_numpy()
        columns.append(pd.DataFrame({band.name: values}))
    scalars = pd.concat(columns, axis=1)
    return parse_tsg.Spectra(
        sensor,
        data[0],
        wavelength,
        info["sample headers"],
        info["class"],
        info["band headers"],
        scalars,
    )


def find_section_files(package_dir: Path, sensor: str) -> tuple[Path, Path] | None:
    header_glob, data_glob = SECTIONS[sensor]
    for header in sorted(package_dir.rglob(header_glob)):
        if sensor == "nir" and header.name.endswith("_tir.tsg"):
            continue
        data = header.with_suffix(".bip")
        if data.exists():
            return header, data
    return None


def band_parameters(
    spectra: np.ndarray, wavelengths: np.ndarray, low: float, high: float, kind: str
) -> tuple[np.ndarray, np.ndarray]:
    """Position (nm) and depth/height per row for one window."""
    keep = (wavelengths >= low) & (wavelengths <= high)
    x = wavelengths[keep]
    positions = np.full(len(spectra), np.nan)
    depths = np.full(len(spectra), np.nan)
    if keep.sum() < 5:
        return positions, depths
    for row, y in enumerate(np.asarray(spectra[:, keep], dtype=float)):
        if not np.isfinite(y).all():
            continue
        if kind == "absorption":
            hull = _upper_hull_indices(x, y)
            continuum = np.interp(x, x[hull], y[hull])
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(continuum > 0, y / continuum, np.nan)
            if not np.isfinite(ratio).any():
                continue  # no positive continuum in this window (e.g. negative reflectance)
            index = int(np.nanargmin(ratio))
            value = 1 - ratio[index]
        else:
            baseline = np.interp(x, [x[0], x[-1]], [y[0], y[-1]])
            excess = y - baseline
            index = int(np.argmax(excess))
            value = excess[index]
            ratio = -excess
        position = x[index]
        if 0 < index < len(x) - 1:
            y0, y1, y2 = ratio[index - 1 : index + 2]
            denominator = y0 - 2 * y1 + y2
            if np.isfinite(denominator) and denominator != 0:
                step = x[index + 1] - x[index]
                position = x[index] + 0.5 * step * (y0 - y2) / denominator
        positions[row] = position
        depths[row] = value
    return positions, depths


def summarise_sensor(
    package_dir: Path,
    hole_id: str,
    sensor: str,
    *,
    width_m: float = 1.0,
    min_readings: int = 3,
    versions: dict | None = None,
) -> tuple[pd.DataFrame, np.ndarray, dict]:
    """1 m medians and band parameters; `versions` collects system-vs-user TSA per metre."""
    files = find_section_files(package_dir, sensor)
    if files is None:
        raise HyLoggerError(f"{hole_id} has no {sensor.upper()} section")
    section = read_section(files[0], files[1], sensor)
    wavelengths = np.asarray(section.wavelength, dtype=np.float64)
    spectra = np.asarray(section.spectra)
    wavelength_grid(spectra, wavelengths)
    frame = _reading_frame(section, hole_id)
    frame, qc = gate_readings(frame, depth_column="depth_m", spectra=spectra, require_mask=True)
    if frame.empty:
        raise HyLoggerError(f"No mask-admitted {sensor.upper()} readings for {hole_id}")
    frame = frame.assign(bin=np.floor(frame.depth_m / width_m).astype(int))
    counts = frame.groupby("bin").size()
    sparse = not (counts >= min_readings).any()
    if sparse:
        # Chips and cuttings: one composite spectrum per sample interval.
        min_readings = 1
    kept = counts[counts >= min_readings].index
    frame = frame[frame.bin.isin(kept)]
    if frame.empty:
        raise HyLoggerError(f"No {sensor.upper()} intervals for {hole_id}")
    if versions is not None:
        versions[sensor] = tsa_versions.per_metre(frame, sensor, width_m)
    medians, rows = [], []
    for bin_id, part in frame.groupby("bin", sort=True):
        medians.append(np.median(spectra[part.source_row.to_numpy()], axis=0))
        rows.append(
            {
                "hole_id": hole_id,
                "sensor": sensor,
                "bin": int(bin_id),
                "depth_from_m": round(bin_id * width_m, 6),
                "depth_to_m": round((bin_id + 1) * width_m, 6),
                "n_readings": int(len(part)),
            }
        )
    median = np.vstack(medians)
    table = pd.DataFrame(rows)
    with np.errstate(all="ignore"):
        table["albedo"] = np.nanmedian(np.where(np.isfinite(median), median, np.nan), axis=1)
    for name, (low, high, kind) in BANDS[sensor].items():
        position, depth = band_parameters(median, wavelengths, low, high, kind)
        table[f"{name}_pos"] = position
        table[f"{name}_{'depth' if kind == 'absorption' else 'height'}"] = depth
    grid = GRIDS[sensor]
    grid = grid[(grid >= wavelengths[0]) & (grid <= wavelengths[-1])]
    resampled = np.vstack([np.interp(grid, wavelengths, row) for row in median]).astype(np.float16)
    qc.update(
        {
            "raw_version": RAW_VERSION,
            "sensor": sensor,
            "source_files": [str(files[0].name), str(files[1].name)],
            "bands_native": int(len(wavelengths)),
            "wavelength_min_nm": float(wavelengths[0]),
            "wavelength_max_nm": float(wavelengths[-1]),
            "grid_bands": int(len(grid)),
            "intervals": int(len(table)),
            "intervals_dropped_sparse": int((counts < min_readings).sum()),
            "min_readings_used": int(min_readings),
            "sparse_sampling": bool(sparse),
            "readings_used": int(len(frame)),
        }
    )
    return table, np.vstack([grid.astype(np.float32)[None, :], resampled.astype(np.float32)]), qc


def summarise_hole(package_dir: Path, hole_id: str, output: Path) -> dict:
    """Write raw_<sensor>.parquet/.npz and summary.json for every sensor present."""
    output.mkdir(parents=True, exist_ok=True)
    results: dict = {"hole_id": hole_id, "package": str(package_dir), "sensors": {}}
    metadata = read_package_metadata(next(package_dir.rglob("*_tsg.tsg")).parent)
    versions: dict[str, pd.DataFrame] = {}
    for sensor in SECTIONS:
        try:
            table, spectra, qc = summarise_sensor(package_dir, hole_id, sensor, versions=versions)
        except HyLoggerError as error:
            results["sensors"][sensor] = {"status": "unavailable", "reason": str(error)}
            continue
        table.to_parquet(output / f"raw_{sensor}.parquet", index=False)
        np.savez_compressed(
            output / f"raw_{sensor}.npz",
            wavelengths=spectra[0],
            spectra=spectra[1:].astype(np.float16),
            bins=table.bin.to_numpy(),
        )
        features = [c for c in table.columns if c.endswith(("_pos", "_depth", "_height"))]
        results["sensors"][sensor] = {
            "status": "summarised",
            "qc": qc,
            "depth_from_m": float(table.depth_from_m.min()),
            "depth_to_m": float(table.depth_to_m.max()),
            "feature_medians": {c: _finite(table[c].median()) for c in features + ["albedo"]},
        }
    compared = pd.concat([v for v in versions.values() if not v.empty] or [pd.DataFrame()])
    if not compared.empty:
        compared.to_parquet(output / "tsa_versions.parquet", index=False)
        results["tsa_versions"] = tsa_versions.summary(compared)
    results["metadata"] = metadata
    (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def _finite(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def load_spectra(output: Path, sensor: str) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    table = pd.read_parquet(output / f"raw_{sensor}.parquet")
    archive = np.load(output / f"raw_{sensor}.npz")
    return table, archive["wavelengths"], archive["spectra"].astype(np.float32)

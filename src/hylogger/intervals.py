"""Turn one hole's raw readings into depth intervals ready for cross-hole analysis.

The unit of analysis is a fixed-width depth interval, not a single reading
and not a whole hole. A reading every few millimetres is noisy and nearly
identical to its neighbours; a whole hole hides where things change down
the core. Half a metre is the scale a geologist reasons in and the scale
assays are sampled at.

Steps, in order:

1. Read the package and gate readings with the quality policy.
2. Bin readings by depth and take the median reflectance per interval.
3. Remove the continuum (hull quotient) so absorption features compare
   across intervals with different overall brightness.
4. Summarise the TSG interpretation per interval (majority label, purity,
   mean weight and error) for validation later. These never enter a model.

Spectral regions stay separate. A SWIR table and a TIR table for the same
hole are two different products.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .errors import HyLoggerError
from .preparation import gate_readings, wavelength_grid
from .quality import QualityPolicy
from .raw import find_package_dir
from .util import unique_snake_columns

REGIONS = {
    # region name -> (pytsg section, label method suffixes in preference order)
    "swir": ("nir", ("u_tsas", "s_tsas")),
    "tir": ("tir", ("uj_clst", "sj_clst")),
}
LABEL_FIELDS = ("grp1", "min1")
CONFIDENCE_FIELDS = ("wt1", "error")
METADATA_KEYS = (
    "hylogger name",
    "scan date",
    "drill date",
    "project name",
    "dataset name",
    "current owner",
    "lat lon",
)


@dataclass
class IntervalTable:
    hole_id: str
    region: str
    wavelengths: np.ndarray
    spectra: np.ndarray
    spectra_raw: np.ndarray
    table: pd.DataFrame
    qc: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.table)


def read_package_metadata(package_dir: Path) -> dict[str, Any]:
    """Acquisition facts from the `[dbextra]` block of the TSG header.

    These identify which scanner produced the data and when. They are the
    columns to test clusters against before believing any of them.
    """
    header = next(package_dir.glob("*_tsg.tsg"), None)
    if header is None:
        return {}
    metadata: dict[str, Any] = {}
    inside = False
    for line in header.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped.lower() == "[dbextra]"
            continue
        if not inside or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip().lower()
        if key in METADATA_KEYS:
            metadata[key.replace(" ", "_")] = value.strip()
    scan = metadata.get("scan_date")
    if scan:
        metadata["scan_year"] = scan[:4]
    return metadata


def continuum_removal(spectra: np.ndarray, wavelengths: np.ndarray) -> np.ndarray:
    """Divide each spectrum by its upper convex hull (hull quotient).

    Output is in (0, 1]: 1 on the hull, lower inside an absorption feature.
    Brightness differences between samples are removed; feature depth and
    position are kept. This is the standard preparation for comparing
    mineral absorption features, and it makes PCA see chemistry rather
    than albedo.
    """
    spectra = np.asarray(spectra, dtype=np.float64)
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    if spectra.ndim != 2 or spectra.shape[1] != len(wavelengths):
        raise HyLoggerError("Spectra and wavelength grid do not match.")
    output = np.empty_like(spectra)
    for row, spectrum in enumerate(spectra):
        hull = _upper_hull_indices(wavelengths, spectrum)
        continuum = np.interp(wavelengths, wavelengths[hull], spectrum[hull])
        with np.errstate(divide="ignore", invalid="ignore"):
            output[row] = np.where(continuum > 0, spectrum / continuum, np.nan)
    return np.clip(output, 0, 1)


def _upper_hull_indices(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Andrew's monotone chain, upper hull only, for x already sorted."""
    hull: list[int] = []
    for index in range(len(x)):
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            cross = (x[b] - x[a]) * (y[index] - y[a]) - (y[b] - y[a]) * (x[index] - x[a])
            if cross >= 0:
                hull.pop()
            else:
                break
        hull.append(index)
    return np.asarray(hull)


def build_intervals(
    config: AppConfig,
    hole_id: str,
    *,
    region: str = "swir",
    interval_m: float = 1.0,
    min_readings: int = 3,
    policy: QualityPolicy | None = None,
    remove_continuum: bool = True,
    band_range: tuple[float, float] | None = None,
) -> IntervalTable:
    """Read one hole and aggregate its readings into depth intervals."""
    if region not in REGIONS:
        raise HyLoggerError(f"region must be one of {', '.join(REGIONS)}.")
    if not np.isfinite(interval_m) or interval_m <= 0 or min_readings < 1:
        raise HyLoggerError("interval_m must be positive.")
    hole_id = hole_id.upper()
    policy = policy or QualityPolicy()
    package_dir = find_package_dir(config, hole_id)
    if package_dir is None:
        raise HyLoggerError(
            f"No extracted raw package is available for {hole_id}. "
            f"Run: hylogger raw fetch {hole_id} --components spectra"
        )

    from pytsg import parse_tsg

    package = parse_tsg.read_package(package_dir, read_cras_file=False)
    section_name, methods = REGIONS[region]
    section = getattr(package, section_name, None)
    if section is None or not hasattr(section, "spectra"):
        raise HyLoggerError(f"{hole_id} has no {region.upper()} section.")

    wavelengths = np.asarray(section.wavelength, dtype=np.float64)
    spectra = np.asarray(section.spectra)
    frame = _reading_frame(section, hole_id)
    if len(frame) != len(spectra):
        raise HyLoggerError("Metadata and spectra row counts differ.")
    wavelength_grid(spectra, wavelengths)
    frame, qc = gate_readings(
        frame, depth_column="depth_m", spectra=spectra, policy=policy, require_mask=True
    )
    qc["readings_in"] = qc["rows_received"]
    qc["duplicate_depth_readings"] = int(frame["depth_m"].duplicated(keep=False).sum())
    qc["duplicate_readings_dropped"] = 0
    if frame.empty:
        raise HyLoggerError(f"No usable {region.upper()} readings remain for {hole_id}.")

    if band_range is not None:
        low, high = band_range
        keep_bands = (wavelengths >= low) & (wavelengths <= high)
        if not keep_bands.any() or not np.isfinite([low, high]).all() or low >= high:
            raise HyLoggerError("Requested band range has no valid wavelength support")
        wavelengths = wavelengths[keep_bands]
        spectra = spectra[:, keep_bands]
    qc["bands"] = int(len(wavelengths))
    qc["wavelength_min_nm"] = float(wavelengths.min())
    qc["wavelength_max_nm"] = float(wavelengths.max())

    # Bin by depth. Interval start is a multiple of the width so intervals
    # line up across holes and against depth-based assay samples.
    frame = frame.assign(_bin=np.floor(frame["depth_m"] / interval_m).astype(int))
    counts = frame.groupby("_bin").size()
    valid_bins = counts[counts >= min_readings].index
    frame = frame[frame["_bin"].isin(valid_bins)]
    qc["intervals_dropped_sparse"] = int((counts < min_readings).sum())
    qc["readings_dropped_sparse"] = int(counts[counts < min_readings].sum())
    if frame.empty:
        raise HyLoggerError(f"No interval in {hole_id} has {min_readings} usable readings.")

    rows = []
    medians = []
    for bin_index, group in frame.groupby("_bin", sort=True):
        medians.append(np.median(spectra[group["source_row"].to_numpy()], axis=0))
        rows.append(_summarise_interval(group, bin_index, interval_m, methods))
    spectra_raw = np.vstack(medians)
    processed = continuum_removal(spectra_raw, wavelengths) if remove_continuum else spectra_raw

    table = pd.DataFrame(rows)
    table.insert(0, "region", region)
    table.insert(0, "hole_id", hole_id)
    for key, value in read_package_metadata(package_dir).items():
        table[key] = value
    qc.update(
        {
            "readings_used": int(len(frame)),
            "intervals": int(len(table)),
            "interval_m": interval_m,
            "min_readings": min_readings,
            "continuum_removed": remove_continuum,
            "depth_min_m": float(table["depth_from"].min()),
            "depth_max_m": float(table["depth_to"].max()),
        }
    )
    return IntervalTable(hole_id, region, wavelengths, processed, spectra_raw, table, qc)


def stack_intervals(tables: list[IntervalTable]) -> IntervalTable:
    """Combine holes into one population. Refuses mismatched grids or regions."""
    if not tables:
        raise HyLoggerError("Nothing to stack.")
    first = tables[0]
    for other in tables[1:]:
        if other.region != first.region:
            raise HyLoggerError("Cannot stack different spectral regions into one population.")
        if other.wavelengths.shape != first.wavelengths.shape or not np.allclose(
            other.wavelengths, first.wavelengths
        ):
            raise HyLoggerError(
                f"{other.hole_id} uses a different wavelength grid from {first.hole_id}. "
                "Align or interpolate explicitly before stacking; do not combine silently."
            )
    return IntervalTable(
        hole_id=",".join(table.hole_id for table in tables),
        region=first.region,
        wavelengths=first.wavelengths,
        spectra=np.vstack([table.spectra for table in tables]),
        spectra_raw=np.vstack([table.spectra_raw for table in tables]),
        table=pd.concat([table.table for table in tables], ignore_index=True),
        qc={table.hole_id: table.qc for table in tables},
    )


def _reading_frame(section: object, hole_id: str) -> pd.DataFrame:
    headers = section.sampleheaders.copy().reset_index(drop=True)
    headers.columns = unique_snake_columns(headers.columns)
    scalars = section.scalars.copy().reset_index(drop=True)
    if len(headers) != len(scalars):
        raise HyLoggerError("Sample headers and scalar rows differ")
    scalars.columns = unique_snake_columns(scalars.columns)
    scalars = scalars.rename(
        columns={
            column: f"scalar_{column}" for column in set(headers.columns) & set(scalars.columns)
        }
    )
    frame = pd.concat([headers, scalars], axis=1)
    frame["hole_id"] = hole_id
    frame["source_row"] = np.arange(len(frame))
    frame["depth_m"] = pd.to_numeric(frame.get("d"), errors="coerce")
    frame = frame.replace({"": np.nan, "null": np.nan, "NULL": np.nan})
    for column in frame.select_dtypes(include=["number"]).columns:
        frame[column] = frame[column].where(frame[column] > -1e38)
    return frame


def _drop_flagged_duplicates(frame: pd.DataFrame, qc: dict[str, Any]) -> pd.DataFrame:
    """Where two readings share a depth, keep the one TSG called Good."""
    duplicated = frame["depth_m"].duplicated(keep=False)
    qc["duplicate_depth_readings"] = int(duplicated.sum())
    diag = frame.get("hy_log_diag")
    if diag is None or not duplicated.any():
        return frame
    flagged = duplicated & diag.astype(str).str.strip().str.lower().ne("good")
    qc["duplicate_readings_dropped"] = int(flagged.sum())
    return frame.loc[~flagged]


def _summarise_interval(
    group: pd.DataFrame, bin_index: int, interval_m: float, methods: tuple[str, ...]
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "depth_from": round(bin_index * interval_m, 4),
        "depth_to": round((bin_index + 1) * interval_m, 4),
        "depth_mid": round((bin_index + 0.5) * interval_m, 4),
        "n_readings": int(len(group)),
        "source_rows": group["source_row"].astype(int).tolist(),
    }
    for method in methods:
        for field_name in LABEL_FIELDS:
            column = f"{field_name}_{method}"
            if column not in group:
                continue
            values = group[column].dropna().astype(str).str.strip()
            values = values[
                ~values.str.upper().isin(
                    {"", "INVALID", "NULL", "ASPECTRAL", "NOTAROK", "NOTAROCK"}
                )
                & pd.to_numeric(values, errors="coerce").isna()
            ]
            if values.empty:
                row[column] = None
                row[f"{column}_purity"] = np.nan
                continue
            counts = values.value_counts()
            row[column] = counts.index[0]
            row[f"{column}_purity"] = float(counts.iloc[0] / len(values))
        for field_name in CONFIDENCE_FIELDS:
            column = f"{field_name}_{method}"
            if column in group:
                row[f"{column}_mean"] = float(pd.to_numeric(group[column], errors="coerce").mean())
    if "hy_log_diag" in group:
        good = group["hy_log_diag"].astype(str).str.strip().str.lower().eq("good")
        row["good_fraction"] = float(good.mean())
    return row


def label_column(region: str, table: pd.DataFrame, field_name: str = "grp1") -> str | None:
    """The preferred TSG label column present in a table, user-level first."""
    for method in REGIONS[region][1]:
        column = f"{field_name}_{method}"
        if column in table and table[column].notna().any():
            return column
    return None

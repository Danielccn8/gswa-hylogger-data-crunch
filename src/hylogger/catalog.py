from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import AppConfig
from .errors import RemoteDataError
from .util import atomic_write_frame, utc_now

CATALOG_COLUMNS = [
    "hole_id",
    "nvcl_available",
    "raw_available",
    "lat",
    "lon",
    "length_m",
    "operator",
    "project",
    "drilling_method",
    "drill_start_date",
    "tenement",
    "raw_size_bytes",
    "raw_modified",
    "raw_url",
]


def http_session(retries: int) -> requests.Session:
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers["User-Agent"] = "gswa-hylogger-toolkit/0.1"
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def parse_thredds_catalog(xml_content: bytes | str, file_base_url: str) -> pd.DataFrame:
    root = ET.fromstring(xml_content)
    namespace = "{http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0}"
    records: list[dict[str, object]] = []
    for dataset in root.iter(f"{namespace}dataset"):
        url_path = dataset.attrib.get("urlPath")
        name = dataset.attrib.get("name", "")
        if not url_path or not name.lower().endswith(".zip"):
            continue
        size_element = dataset.find(f"{namespace}dataSize")
        date_element = dataset.find(f"{namespace}date")
        size_bytes = _size_in_bytes(
            size_element.text if size_element is not None else None,
            size_element.attrib.get("units") if size_element is not None else None,
        )
        records.append(
            {
                "hole_id": Path(name).stem.upper(),
                "raw_available": True,
                "raw_size_bytes": size_bytes,
                "raw_modified": date_element.text if date_element is not None else None,
                "raw_url": urljoin(file_base_url.rstrip("/") + "/", url_path.lstrip("/")),
            }
        )
    if not records:
        raise RemoteDataError("The THREDDS catalog did not contain any ZIP datasets.")
    return pd.DataFrame.from_records(records).drop_duplicates("hole_id", keep="last")


def fetch_raw_catalog(config: AppConfig) -> pd.DataFrame:
    session = http_session(config.retries)
    response = session.get(config.raw_catalog_url, timeout=config.request_timeout_seconds)
    response.raise_for_status()
    return parse_thredds_catalog(response.content, config.raw_file_base_url)


def fetch_nvcl_catalog(config: AppConfig) -> pd.DataFrame:
    from nvcl_kit.param_builder import param_builder
    from nvcl_kit.reader import NVCLReader

    reader = NVCLReader(
        param_builder(config.nvcl_provider, max_boreholes=0),
        retries=config.retries,
        retry_backoff=1,
    )
    rows = [vars(hole) for hole in reader.get_boreholes_list()]
    if not rows:
        raise RemoteDataError("The NVCL service returned no boreholes.")
    frame = pd.DataFrame.from_records(rows).rename(
        columns={
            "nvcl_id": "hole_id",
            "x": "lon",
            "y": "lat",
            "boreholeLength_m": "length_m",
            "drillingMethod": "drilling_method",
            "drillStartDate": "drill_start_date",
        }
    )
    keep = [
        "hole_id",
        "lat",
        "lon",
        "length_m",
        "operator",
        "project",
        "drilling_method",
        "drill_start_date",
        "tenement",
    ]
    frame = frame[[column for column in keep if column in frame.columns]].copy()
    frame["hole_id"] = frame["hole_id"].astype(str).str.upper().str.strip()
    for column in ("lat", "lon", "length_m"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["nvcl_available"] = True
    return frame.drop_duplicates("hole_id", keep="last")


def refresh_catalog(config: AppConfig) -> dict[str, object]:
    config.ensure_layout()
    warnings: list[str] = []
    nvcl: pd.DataFrame | None = None
    raw: pd.DataFrame | None = None

    try:
        nvcl = fetch_nvcl_catalog(config)
        atomic_write_frame(nvcl, config.data_dir / "catalog" / "nvcl_holes.parquet")
        atomic_write_frame(nvcl, config.data_dir / "catalog" / "nvcl_holes.csv")
    except Exception as exc:  # provider failures must not discard a good cached snapshot
        warnings.append(f"NVCL refresh failed: {exc}")
        nvcl = _load_optional(config.data_dir / "catalog" / "nvcl_holes.parquet")

    try:
        raw = fetch_raw_catalog(config)
        atomic_write_frame(raw, config.data_dir / "catalog" / "raw_packages.parquet")
        atomic_write_frame(raw, config.data_dir / "catalog" / "raw_packages.csv")
    except Exception as exc:
        warnings.append(f"THREDDS refresh failed: {exc}")
        raw = _load_optional(config.data_dir / "catalog" / "raw_packages.parquet")

    if nvcl is None and raw is None:
        raise RemoteDataError("Neither provider returned data and no cached catalog exists.")

    nvcl = nvcl if nvcl is not None else pd.DataFrame(columns=["hole_id"])
    raw = raw if raw is not None else pd.DataFrame(columns=["hole_id"])
    merged = nvcl.merge(raw, on="hole_id", how="outer", suffixes=("", "_raw"))
    for column in ("nvcl_available", "raw_available"):
        if column not in merged:
            merged[column] = False
        merged[column] = merged[column].fillna(False).astype(bool)
    for column in CATALOG_COLUMNS:
        if column not in merged:
            merged[column] = None
    merged = merged[CATALOG_COLUMNS].sort_values("hole_id").reset_index(drop=True)
    merged["catalog_refreshed_at"] = utc_now()
    atomic_write_frame(merged, config.data_dir / "catalog" / "catalog.parquet")
    atomic_write_frame(merged, config.data_dir / "catalog" / "catalog.csv")
    return {
        "catalog_rows": len(merged),
        "nvcl_rows": int(merged["nvcl_available"].sum()),
        "raw_rows": int(merged["raw_available"].sum()),
        "warnings": warnings,
    }


def load_catalog(config: AppConfig) -> pd.DataFrame:
    path = config.data_dir / "catalog" / "catalog.parquet"
    if not path.exists():
        raise RemoteDataError("No local catalog exists. Run: hylogger catalog refresh")
    return pd.read_parquet(path)


def raw_record(config: AppConfig, hole_id: str) -> dict[str, object] | None:
    catalog = load_catalog(config)
    match = catalog[catalog["hole_id"].astype(str).str.upper() == hole_id.upper()]
    return None if match.empty else match.iloc[0].to_dict()


def _load_optional(path: Path) -> pd.DataFrame | None:
    return pd.read_parquet(path) if path.exists() else None


def _size_in_bytes(value: str | None, unit: str | None) -> int | None:
    if not value:
        return None
    multipliers = {
        "bytes": 1,
        "kbytes": 1000,
        "mbytes": 1000**2,
        "gbytes": 1000**3,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
    }
    try:
        return int(float(value) * multipliers.get((unit or "bytes").lower(), 1))
    except ValueError:
        return None

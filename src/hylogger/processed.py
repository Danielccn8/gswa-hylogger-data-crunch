from __future__ import annotations

import fnmatch
import re
from io import StringIO
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .errors import RemoteDataError
from .manifest import Manifest
from .util import (
    atomic_write_frame,
    atomic_write_json,
    sha256_file,
    unique_snake_columns,
    utc_now,
)


def create_reader(config: AppConfig):
    from nvcl_kit.param_builder import param_builder
    from nvcl_kit.reader import NVCLReader

    return NVCLReader(
        param_builder(config.nvcl_provider, max_boreholes=0),
        skip_bhlist=True,
        retries=config.retries,
        retry_backoff=1,
    )


def inventory_hole(config: AppConfig, hole_id: str, *, persist: bool = True) -> dict[str, Any]:
    hole_id = hole_id.upper()
    reader = create_reader(config)
    datasets = reader.get_dataset_list(hole_id)
    if not datasets:
        raise RemoteDataError(
            f"NVCL returned no datasets for {hole_id}. The service may be unavailable, "
            "or the hole may not have processed data."
        )

    dataset_records: list[dict[str, Any]] = []
    for dataset in datasets:
        dataset_record = _public_attributes(dataset)
        dataset_id = dataset_record.get("dataset_id")
        logs = reader.get_scalar_logs(dataset_id) if dataset_id else []
        dataset_record["logs"] = [describe_log(log) for log in logs]
        dataset_record["log_count"] = len(logs)
        dataset_records.append(dataset_record)

    inventory = {
        "hole_id": hole_id,
        "retrieved_at": utc_now(),
        "dataset_count": len(dataset_records),
        "datasets": dataset_records,
    }
    if persist:
        path = config.data_dir / "downloads" / "processed" / hole_id / "inventory.json"
        atomic_write_json(path, inventory)
    return inventory


def describe_log(log: object) -> dict[str, Any]:
    record = _public_attributes(log)
    name = str(record.get("log_name", ""))
    record["processing_level"] = processing_level(name)
    record["spectral_region"] = spectral_region(name)
    return record


def processing_level(log_name: str) -> str:
    if re.search(r"(?:^|\s)s[A-Z]", log_name):
        return "L1-system"
    if re.search(r"(?:^|\s)u[A-Z]", log_name):
        return "L2-user"
    if re.search(r"(?:^|\s)d[A-Z]", log_name):
        return "L3-domain"
    return "unspecified"


def spectral_region(log_name: str) -> str:
    upper = log_name.upper()
    if "TSAS" in upper or "SWIR" in upper:
        return "SWIR"
    if "TSAV" in upper or "VNIR" in upper:
        return "VNIR"
    if "TSAT" in upper or "TIR" in upper:
        return "TIR"
    if "MIR" in upper:
        return "MIR"
    return "unspecified"


def fetch_processed_hole(
    config: AppConfig,
    hole_id: str,
    *,
    profile: str = "clustering",
    all_logs: bool = False,
    names: list[str] | None = None,
) -> list[dict[str, Any]]:
    hole_id = hole_id.upper()
    config.ensure_layout()
    manifest = Manifest(config.manifest_path)
    reader = create_reader(config)
    inventory = inventory_hole(config, hole_id)
    outputs: list[dict[str, Any]] = []

    for dataset in inventory["datasets"]:
        dataset_id = str(dataset["dataset_id"])
        selected = select_logs(
            dataset.get("logs", []),
            config=config,
            profile=profile,
            all_logs=all_logs,
            names=names,
        )
        if not selected:
            continue
        artifact_key = f"processed:{hole_id}:{dataset_id}"
        folder = config.data_dir / "downloads" / "processed" / hole_id / dataset_id
        raw_path = folder / "scalar_logs.csv"
        parquet_path = folder / "scalar_logs.parquet"
        metadata_path = folder / "logs.json"
        started = utc_now()
        manifest.record(
            artifact_key=artifact_key,
            hole_id=hole_id,
            kind="processed",
            dataset_id=dataset_id,
            status="downloading",
            started_at=started,
            metadata={"profile": profile, "log_count": len(selected)},
        )
        try:
            csv_content = reader.get_scalar_data([record["log_id"] for record in selected])
            if not csv_content:
                raise RemoteDataError(
                    f"NVCL returned no scalar data for {hole_id}, dataset {dataset_id}."
                )
            if isinstance(csv_content, bytes):
                csv_content = csv_content.decode("utf-8", errors="replace")
            folder.mkdir(parents=True, exist_ok=True)
            temporary_csv = raw_path.with_name(f".{raw_path.name}.tmp")
            temporary_csv.write_text(csv_content, encoding="utf-8")
            temporary_csv.replace(raw_path)

            frame = normalise_scalar_csv(csv_content, hole_id=hole_id, dataset_id=dataset_id)
            atomic_write_frame(frame, parquet_path)
            atomic_write_json(
                metadata_path,
                {
                    "hole_id": hole_id,
                    "dataset": {key: value for key, value in dataset.items() if key != "logs"},
                    "profile": "all" if all_logs else profile,
                    "logs": selected,
                    "downloaded_at": utc_now(),
                },
            )
            checksum = sha256_file(raw_path)
            manifest.record(
                artifact_key=artifact_key,
                hole_id=hole_id,
                kind="processed",
                dataset_id=dataset_id,
                status="complete",
                local_path=parquet_path,
                size_bytes=raw_path.stat().st_size,
                sha256=checksum,
                started_at=started,
                completed_at=utc_now(),
                metadata={"profile": profile, "log_count": len(selected), "rows": len(frame)},
            )
            outputs.append(
                {
                    "hole_id": hole_id,
                    "dataset_id": dataset_id,
                    "logs": len(selected),
                    "rows": len(frame),
                    "path": str(parquet_path),
                }
            )
        except Exception as exc:
            manifest.record(
                artifact_key=artifact_key,
                hole_id=hole_id,
                kind="processed",
                dataset_id=dataset_id,
                status="failed",
                started_at=started,
                error=str(exc),
                metadata={"profile": profile, "log_count": len(selected)},
            )
            raise

    if not outputs:
        raise RemoteDataError(
            f"No logs for {hole_id} matched profile {profile!r}. "
            "Run 'hylogger processed preview' or use --all-logs."
        )
    return outputs


def select_logs(
    logs: list[dict[str, Any]],
    *,
    config: AppConfig,
    profile: str,
    all_logs: bool,
    names: list[str] | None,
) -> list[dict[str, Any]]:
    if all_logs:
        return logs
    if names:
        wanted = {name.casefold() for name in names}
        return [log for log in logs if str(log.get("log_name", "")).casefold() in wanted]
    profile_config = config.profiles.get(profile)
    if not profile_config:
        choices = ", ".join(sorted(config.profiles)) or "none configured"
        raise RemoteDataError(f"Unknown processed profile {profile!r}. Available: {choices}")
    patterns = profile_config.get("patterns", [])
    return [
        log
        for log in logs
        if any(
            fnmatch.fnmatchcase(str(log.get("log_name", "")).casefold(), pattern.casefold())
            for pattern in patterns
        )
    ]


def normalise_scalar_csv(csv_content: str, *, hole_id: str, dataset_id: str) -> pd.DataFrame:
    frame = pd.read_csv(StringIO(csv_content), low_memory=False)
    frame = frame.replace({"null": np.nan, "NULL": np.nan, "": np.nan})
    frame.columns = unique_snake_columns(frame.columns)
    numeric_columns = frame.select_dtypes(include=["number"]).columns
    for column in numeric_columns:
        frame[column] = frame[column].where(frame[column] > -1e38)
    frame.insert(0, "dataset_id", dataset_id)
    frame.insert(0, "hole_id", hole_id)
    return frame


def _public_attributes(value: object) -> dict[str, Any]:
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_") and not callable(item)
        }
    return {"value": str(value)}

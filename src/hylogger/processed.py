from __future__ import annotations

import fnmatch
import hashlib
import re
import shutil
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


def download_scalars(config: AppConfig, log_ids: list[str], *, workers: int = 4) -> str:
    """Download scalar logs one log per request and join them column-wise.

    The WA NVCL service answers single-log requests in seconds but returns 502
    after about two minutes for multi-log requests, which is what nvcl-kit
    sends. Every response has the same StartDepth/EndDepth rows in the same
    order; that is verified before the value columns are joined.
    """
    from concurrent.futures import ThreadPoolExecutor

    from nvcl_kit.param_builder import param_builder

    from .catalog import http_session

    url = param_builder(config.nvcl_provider, max_boreholes=0).NVCL_URL.rstrip("/")
    url += "/downloadscalars.html"
    session = http_session(config.retries)

    def one(log_id: str) -> pd.DataFrame:
        response = session.get(
            url, params={"logid": log_id}, timeout=(15, config.request_timeout_seconds)
        )
        response.raise_for_status()
        text = response.text
        if not text.startswith("StartDepth"):
            raise RemoteDataError(f"Unexpected scalar response for log {log_id}: {text[:80]!r}")
        return pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)

    with ThreadPoolExecutor(max(1, min(workers, len(log_ids)))) as pool:
        parts = list(pool.map(one, log_ids))
    if not parts:
        return ""
    depths = parts[0].iloc[:, :2]
    for log_id, part in zip(log_ids, parts, strict=True):
        if part.shape[0] != depths.shape[0] or not part.iloc[:, :2].equals(depths):
            raise RemoteDataError(f"Depth rows of log {log_id} differ from the first log")
    joined = pd.concat([depths, *[part.iloc[:, 2:] for part in parts]], axis=1)
    return joined.to_csv(index=False, lineterminator="\n")


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
    if re.search(r"(?:^|\s)s(?:[A-Z]|jCLST\b)", log_name):
        return "L1-system"
    if re.search(r"(?:^|\s)u(?:[A-Z]|jCLST\b)", log_name):
        return "L2-user"
    if re.search(r"(?:^|\s)d(?:[A-Z]|jCLST\b)", log_name):
        return "L3-domain"
    return "unspecified"


def spectral_region(log_name: str) -> str:
    upper = log_name.upper()
    if "TSAS" in upper or "SWIR" in upper:
        return "SWIR"
    if "TSAV" in upper or "VNIR" in upper:
        return "VNIR"
    if "TSAT" in upper or "JCLST" in upper or "TIR" in upper:
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
            csv_content = download_scalars(config, [record["log_id"] for record in selected])
            if not csv_content:
                raise RemoteDataError(
                    f"NVCL returned no scalar data for {hole_id}, dataset {dataset_id}."
                )
            if isinstance(csv_content, bytes):
                csv_content = csv_content.decode("utf-8", errors="replace")
            folder.mkdir(parents=True, exist_ok=True)
            frame = normalise_scalar_csv(csv_content, hole_id=hole_id, dataset_id=dataset_id)
            # Save immutable source responses before touching the selected table.
            checksum = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()
            snapshot_key = hashlib.sha256(f"{hole_id}:{dataset_id}:{checksum}".encode()).hexdigest()
            snapshot = config.data_dir / "snapshots" / snapshot_key
            snapshot.mkdir(parents=True, exist_ok=True)
            if not (snapshot / "scalar_logs.csv").exists():
                (snapshot / "scalar_logs.csv").write_text(csv_content, encoding="utf-8")
            if not (snapshot / "scalar_logs.parquet").exists():
                atomic_write_frame(frame, snapshot / "scalar_logs.parquet")
            receipt = {
                "hole_id": hole_id,
                "dataset": {key: value for key, value in dataset.items() if key != "logs"},
                "profile": "all" if all_logs else profile,
                "logs": selected,
                "downloaded_at": utc_now(),
                "source_sha256": checksum,
            }
            if not (snapshot / "logs.json").exists():
                atomic_write_json(snapshot / "logs.json", receipt)
            activate = True
            if parquet_path.exists():
                previous = pd.read_parquet(parquet_path)
                legacy_hash = sha256_file(raw_path if raw_path.exists() else parquet_path)
                archive_key = hashlib.sha256(
                    f"{hole_id}:{dataset_id}:{legacy_hash}".encode()
                ).hexdigest()
                archive = config.data_dir / "snapshots" / archive_key
                archive.mkdir(parents=True, exist_ok=True)
                for prior in (raw_path, parquet_path, metadata_path):
                    if prior.exists() and not (archive / prior.name).exists():
                        shutil.copy2(prior, archive / prior.name)
                # A narrower request is a separate snapshot, not a destructive
                # replacement. Never join logs by row position across responses.
                activate = set(previous.columns).issubset(frame.columns)
            if not activate:
                manifest.record(
                    artifact_key=artifact_key,
                    hole_id=hole_id,
                    kind="processed",
                    dataset_id=dataset_id,
                    status="complete",
                    local_path=parquet_path,
                    completed_at=utc_now(),
                    sha256=sha256_file(parquet_path),
                    metadata={
                        "preserved_selected_source": True,
                        "rows": len(previous),
                        "columns": list(previous.columns),
                    },
                )
                manifest.record(
                    artifact_key=f"{artifact_key}:snapshot:{checksum}",
                    hole_id=hole_id,
                    kind="processed_snapshot",
                    dataset_id=dataset_id,
                    status="complete",
                    local_path=snapshot / "scalar_logs.parquet",
                    sha256=checksum,
                    started_at=started,
                    completed_at=utc_now(),
                    metadata={"selected": False, "reason": "narrower log coverage"},
                )
                outputs.append(
                    {
                        "hole_id": hole_id,
                        "dataset_id": dataset_id,
                        "logs": len(selected),
                        "rows": len(frame),
                        "path": str(snapshot / "scalar_logs.parquet"),
                        "selected": False,
                    }
                )
                continue
            temporary_csv = raw_path.with_name(f".{raw_path.name}.tmp")
            temporary_csv.write_text(csv_content, encoding="utf-8")
            temporary_csv.replace(raw_path)
            atomic_write_frame(frame, parquet_path)
            atomic_write_json(
                metadata_path,
                receipt,
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

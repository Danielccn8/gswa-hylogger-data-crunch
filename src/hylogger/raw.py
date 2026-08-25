from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin

import requests

from .catalog import http_session, raw_record
from .config import AppConfig
from .errors import HyLoggerError, RemoteDataError
from .manifest import Manifest
from .util import human_bytes, sha256_file, utc_now


def resolve_raw_url(config: AppConfig, hole_id: str) -> tuple[str, int | None]:
    hole_id = hole_id.upper()
    try:
        record = raw_record(config, hole_id)
    except RemoteDataError:
        record = None
    if record and record.get("raw_url") and bool(record.get("raw_available")):
        size = record.get("raw_size_bytes")
        return str(record["raw_url"]), int(size) if size is not None else None
    fallback = urljoin(config.raw_file_base_url.rstrip("/") + "/", f"rs07/WA/{hole_id}.zip")
    return fallback, None


def probe_raw(config: AppConfig, hole_id: str) -> dict[str, Any]:
    url, catalog_size = resolve_raw_url(config, hole_id)
    session = http_session(config.retries)
    response = session.head(url, allow_redirects=True, timeout=config.request_timeout_seconds)
    if response.status_code == 405:
        response = session.get(
            url,
            headers={"Range": "bytes=0-0"},
            stream=True,
            timeout=config.request_timeout_seconds,
        )
    response.raise_for_status()
    size = response.headers.get("Content-Length")
    return {
        "hole_id": hole_id.upper(),
        "url": response.url,
        "size_bytes": int(size) if size and response.status_code != 206 else catalog_size,
        "catalog_size_bytes": catalog_size,
        "accept_ranges": response.headers.get("Accept-Ranges", "").lower() == "bytes",
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "content_type": response.headers.get("Content-Type"),
    }


def download_raw_hole(
    config: AppConfig,
    hole_id: str,
    *,
    components: set[str],
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    hole_id = hole_id.upper()
    config.ensure_layout()
    manifest = Manifest(config.manifest_path)
    probe = probe_raw(config, hole_id)
    expected_size = probe["size_bytes"] or probe["catalog_size_bytes"]
    folder = config.data_dir / "downloads" / "raw" / hole_id
    archive = folder / "archive" / f"{hole_id}.zip"
    extracted = folder / "extracted"
    artifact_key = f"raw:{hole_id}"

    if dry_run:
        return {
            **probe,
            "archive": str(archive),
            "components": sorted(components),
            "dry_run": True,
        }

    existing_manifest = manifest.get(artifact_key)
    if archive.exists() and not force:
        if expected_size is None or archive.stat().st_size == expected_size:
            checksum = existing_manifest.get("sha256") if existing_manifest else None
            checksum = checksum or sha256_file(archive, config.chunk_size_bytes)
            verification = extract_archive(archive, extracted, components) if components else None
            manifest.record(
                artifact_key=artifact_key,
                hole_id=hole_id,
                kind="raw",
                status="complete",
                source_url=probe["url"],
                local_path=archive,
                size_bytes=archive.stat().st_size,
                sha256=checksum,
                etag=probe["etag"],
                last_modified=probe["last_modified"],
                completed_at=utc_now(),
                metadata={"components": sorted(components), "verification": verification},
            )
            return {
                "hole_id": hole_id,
                "archive": str(archive),
                "size_bytes": archive.stat().st_size,
                "sha256": checksum,
                "resumed": False,
                "already_present": True,
                "verification": verification,
            }

    archive.parent.mkdir(parents=True, exist_ok=True)
    part = archive.with_suffix(".zip.part")
    resume_from = part.stat().st_size if part.exists() and not force else 0
    if force and part.exists():
        part.unlink()
    remaining = max(0, (expected_size or 0) - resume_from)
    free = shutil.disk_usage(folder.parent).free
    required = remaining + config.minimum_free_space_bytes
    if expected_size is not None and free < required:
        raise HyLoggerError(
            f"Not enough free space for {hole_id}. Need approximately {human_bytes(required)}, "
            f"available {human_bytes(free)}. Change HYLOGGER_DATA_DIR to a larger drive."
        )

    started = utc_now()
    manifest.record(
        artifact_key=artifact_key,
        hole_id=hole_id,
        kind="raw",
        status="downloading",
        source_url=probe["url"],
        local_path=part,
        size_bytes=resume_from,
        etag=probe["etag"],
        last_modified=probe["last_modified"],
        started_at=started,
        metadata={"expected_size": expected_size, "components": sorted(components)},
    )

    try:
        resumed = _stream_download(
            config,
            url=probe["url"],
            part=part,
            resume_from=resume_from,
            expected_size=expected_size,
        )
        if expected_size is not None and part.stat().st_size != expected_size:
            raise RemoteDataError(
                f"Downloaded size for {hole_id} is {part.stat().st_size:,} bytes; "
                f"expected {expected_size:,}. The .part file was kept for resumption."
            )
        os.replace(part, archive)
        checksum = sha256_file(archive, config.chunk_size_bytes)
        verification = extract_archive(archive, extracted, components) if components else None
        manifest.record(
            artifact_key=artifact_key,
            hole_id=hole_id,
            kind="raw",
            status="complete",
            source_url=probe["url"],
            local_path=archive,
            size_bytes=archive.stat().st_size,
            sha256=checksum,
            etag=probe["etag"],
            last_modified=probe["last_modified"],
            started_at=started,
            completed_at=utc_now(),
            metadata={"components": sorted(components), "verification": verification},
        )
        return {
            "hole_id": hole_id,
            "archive": str(archive),
            "size_bytes": archive.stat().st_size,
            "sha256": checksum,
            "resumed": resumed,
            "already_present": False,
            "verification": verification,
        }
    except Exception as exc:
        manifest.record(
            artifact_key=artifact_key,
            hole_id=hole_id,
            kind="raw",
            status="failed",
            source_url=probe["url"],
            local_path=part,
            size_bytes=part.stat().st_size if part.exists() else resume_from,
            started_at=started,
            error=str(exc),
            metadata={"expected_size": expected_size, "components": sorted(components)},
        )
        raise


def import_raw_archive(
    config: AppConfig,
    source: Path,
    *,
    hole_id: str | None,
    mode: str,
    components: set[str],
    package_dir: Path | None = None,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise HyLoggerError(f"Raw source must be an existing ZIP file: {source}")
    resolved_hole = (hole_id or source.stem).upper()
    config.ensure_layout()
    folder = config.data_dir / "downloads" / "raw" / resolved_hole
    if mode == "copy":
        target = folder / "archive" / f"{resolved_hole}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.resolve() != source:
            raise HyLoggerError(f"Import target already exists: {target}")
        if target.resolve() != source:
            shutil.copy2(source, target)
        archive = target
    elif mode == "reference":
        archive = source
    else:
        raise HyLoggerError("Import mode must be 'reference' or 'copy'.")

    checksum = sha256_file(archive, config.chunk_size_bytes)
    external_package_dir = None
    if package_dir is not None:
        external_root = package_dir.expanduser().resolve()
        header = next(external_root.rglob("*_tsg.tsg"), None) if external_root.exists() else None
        if header is None:
            raise HyLoggerError(f"No extracted TSG package found under: {external_root}")
        external_package_dir = header.parent
        verification = verify_extracted_package(external_package_dir)
    else:
        verification = (
            extract_archive(archive, folder / "extracted", components) if components else None
        )
    Manifest(config.manifest_path).record(
        artifact_key=f"raw:{resolved_hole}",
        hole_id=resolved_hole,
        kind="raw",
        status="complete",
        source_url=f"file://{source}",
        local_path=archive,
        size_bytes=archive.stat().st_size,
        sha256=checksum,
        completed_at=utc_now(),
        metadata={
            "import_mode": mode,
            "components": sorted(components),
            "verification": verification,
            "external_package_dir": str(external_package_dir) if external_package_dir else None,
        },
    )
    return {
        "hole_id": resolved_hole,
        "archive": str(archive),
        "size_bytes": archive.stat().st_size,
        "sha256": checksum,
        "verification": verification,
    }


def extract_archive(archive: Path, target: Path, components: set[str]) -> dict[str, Any]:
    target.mkdir(parents=True, exist_ok=True)
    extracted_files = 0
    skipped_files = 0
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            if member.is_dir() or not _component_match(member.filename, components):
                continue
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise HyLoggerError(f"Unsafe path in ZIP archive: {member.filename}")
            destination = target.joinpath(*relative.parts)
            resolved = destination.resolve()
            if target.resolve() not in resolved.parents:
                raise HyLoggerError(f"Unsafe extraction target: {member.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.stat().st_size == member.file_size:
                skipped_files += 1
                continue
            with package.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
            extracted_files += 1
    verification = verify_extracted_package(target)
    return {
        "extracted_files": extracted_files,
        "unchanged_files": skipped_files,
        **verification,
    }


def verify_extracted_package(target: Path) -> dict[str, Any]:
    headers = sorted(target.rglob("*_tsg.tsg"))
    if not headers:
        return {"pytsg_verified": False, "reason": "No NIR/SWIR TSG header was extracted."}
    from pytsg import parse_tsg

    package_dir = headers[0].parent
    package = parse_tsg.read_package(package_dir, read_cras_file=False)
    output: dict[str, Any] = {"pytsg_verified": hasattr(package.nir, "spectra")}
    for name in ("nir", "tir", "mir"):
        section = getattr(package, name, None)
        if section is not None and hasattr(section, "spectra"):
            output[f"{name}_shape"] = list(section.spectra.shape)
    output["package_dir"] = str(package_dir)
    return output


def find_raw_archive(config: AppConfig, hole_id: str) -> Path | None:
    manifest = Manifest(config.manifest_path).get(f"raw:{hole_id.upper()}")
    if manifest and manifest.get("local_path"):
        path = Path(manifest["local_path"])
        if path.exists() and path.suffix.lower() == ".zip":
            return path
    path = (
        config.data_dir
        / "downloads"
        / "raw"
        / hole_id.upper()
        / "archive"
        / f"{hole_id.upper()}.zip"
    )
    return path if path.exists() else None


def find_package_dir(config: AppConfig, hole_id: str) -> Path | None:
    manifest = Manifest(config.manifest_path).get(f"raw:{hole_id.upper()}")
    if manifest:
        external = manifest.get("metadata", {}).get("external_package_dir")
        if external:
            path = Path(external)
            if path.exists() and next(path.glob("*_tsg.tsg"), None):
                return path
    root = config.data_dir / "downloads" / "raw" / hole_id.upper() / "extracted"
    if not root.exists():
        return None
    header = next(root.rglob("*_tsg.tsg"), None)
    return header.parent if header else None


def parse_components(value: str | None, default: list[str] | None = None) -> set[str]:
    values = value.split(",") if value is not None else (default or [])
    components = {item.strip().lower() for item in values if item.strip()}
    valid = {"spectra", "images", "cras", "lidar", "docs", "all", "none"}
    unknown = components - valid
    if unknown:
        raise HyLoggerError(f"Unknown raw components: {', '.join(sorted(unknown))}")
    if "none" in components:
        return set()
    return components


def _stream_download(
    config: AppConfig,
    *,
    url: str,
    part: Path,
    resume_from: int,
    expected_size: int | None,
) -> bool:
    session = http_session(config.retries)
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    response = session.get(
        url,
        headers=headers,
        stream=True,
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()
    resumed = resume_from > 0 and response.status_code == requests.codes.partial_content
    mode = "ab" if resumed else "wb"
    downloaded = resume_from if resumed else 0
    next_report = downloaded + 256 * 1024**2
    with part.open(mode) as output:
        for chunk in response.iter_content(chunk_size=config.chunk_size_bytes):
            if not chunk:
                continue
            output.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_report:
                total = f" / {human_bytes(expected_size)}" if expected_size else ""
                print(f"  downloaded {human_bytes(downloaded)}{total}")
                next_report += 256 * 1024**2
    return resumed


def _component_match(filename: str, components: set[str]) -> bool:
    if "all" in components:
        return True
    lower = filename.lower()
    suffix = Path(lower).suffix
    is_cras = "cras" in lower
    if "cras" in components and is_cras:
        return True
    if "lidar" in components and ("lidar" in lower or "hires.dat" in lower):
        return True
    if "images" in components and suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        return True
    if "docs" in components and suffix in {".xml", ".txt", ".pdf", ".csv"}:
        return True
    if "spectra" in components:
        return suffix in {".tsg", ".bip", ".cal", ".ini", ".dat"} and not is_cras
    return False

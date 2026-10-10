"""Fetch only the spectral members of a raw HyLogger ZIP using HTTP byte ranges.

A package ZIP is mostly core imagery (CRAS and JPEG). The NIR/TIR spectra and
their TSG headers are typically 10-30% of it. NCI THREDDS honours explicit
`Range: bytes=a-b` requests (suffix ranges return 400), so the ZIP central
directory and the selected members are read remotely. `zipfile` verifies
each member's CRC-32 while extracting.
"""

from __future__ import annotations

import io
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from ..config import AppConfig
from ..errors import HyLoggerError
from ..manifest import Manifest
from ..raw import _component_match, probe_raw, verify_extracted_package
from ..util import utc_now


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP Range requests with a read-ahead buffer."""

    def __init__(
        self,
        url: str,
        size: int,
        *,
        session: requests.Session | None = None,
        block: int = 8 << 20,
        timeout: float = 180,
        retries: int = 5,
    ) -> None:
        self.url, self.size, self.block, self.timeout, self.retries = (
            url,
            size,
            block,
            timeout,
            retries,
        )
        self.session = session or requests.Session()
        self.position = 0
        self.buffer_start = -1
        self.buffer = b""
        self.bytes_fetched = 0
        self.requests = 0
        self.segment: tuple[int, int, Any] | None = None  # (start, end, temp file)

    def prefetch(self, start: int, end: int, *, part: int = 16 << 20, workers: int = 4) -> None:
        """Download [start, end] with parallel ranges into a temp file served by reads.

        Each request costs ~14 s before the first byte on THREDDS, so one large
        member is fetched as many concurrent parts rather than sequentially.
        """
        import tempfile
        from concurrent.futures import ThreadPoolExecutor

        self.drop_prefetch()
        end = min(end, self.size - 1)
        handle = tempfile.TemporaryFile()
        handle.truncate(end - start + 1)
        lock = __import__("threading").Lock()
        spans = [(s, min(end, s + part - 1)) for s in range(start, end + 1, part)]

        def get(span: tuple[int, int]) -> None:
            data = self._fetch(*span)
            with lock:
                handle.seek(span[0] - start)
                handle.write(data)

        with ThreadPoolExecutor(min(workers, len(spans))) as pool:
            list(pool.map(get, spans))
        self.segment = (start, end, handle)

    def drop_prefetch(self) -> None:
        if self.segment is not None:
            self.segment[2].close()
            self.segment = None

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence]
        self.position = max(0, min(self.size, base + offset))
        return self.position

    def _fetch(self, start: int, end: int) -> bytes:
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(
                    self.url,
                    headers={"Range": f"bytes={start}-{end}"},
                    timeout=(20, self.timeout),
                )
                if response.status_code != 206:
                    raise HyLoggerError(
                        f"Server did not honour a byte range (HTTP {response.status_code})"
                    )
                data = response.content
                if len(data) != end - start + 1:
                    raise HyLoggerError("Short byte-range response")
                self.bytes_fetched += len(data)
                self.requests += 1
                return data
            except (requests.RequestException, HyLoggerError):
                if attempt == self.retries:
                    raise
                # THREDDS resets connections in bursts; back off long enough to outlast one.
                time.sleep(min(120, 5 * 3**attempt))
        raise AssertionError("unreachable")

    def readinto(self, target) -> int:
        wanted = len(target)
        if self.position >= self.size or wanted == 0:
            return 0
        end = min(self.size, self.position + wanted) - 1
        if self.segment and self.segment[0] <= self.position <= self.segment[1]:
            start, stop, handle = self.segment
            handle.seek(self.position - start)
            chunk = handle.read(min(end, stop) - self.position + 1)
            target[: len(chunk)] = chunk
            self.position += len(chunk)
            return len(chunk)
        inside = self.buffer_start <= self.position and end < self.buffer_start + len(self.buffer)
        if not inside:
            # Large reads (member bodies) fetch exactly; small reads use read-ahead.
            fetch_end = (
                end if wanted >= self.block else min(self.size - 1, self.position + self.block - 1)
            )
            self.buffer = self._fetch(self.position, fetch_end)
            self.buffer_start = self.position
        offset = self.position - self.buffer_start
        chunk = self.buffer[offset : offset + (end - self.position + 1)]
        target[: len(chunk)] = chunk
        self.position += len(chunk)
        return len(chunk)


def spectral_members(
    package: zipfile.ZipFile, *, include_lidar: bool = False
) -> list[zipfile.ZipInfo]:
    members = []
    for info in package.infolist():
        if info.is_dir() or not _component_match(info.filename, {"spectra"}):
            continue
        if not include_lidar and info.filename.lower().endswith("hires.dat"):
            continue
        members.append(info)
    return members


def _extract(
    package: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    target: Path,
    remote: HttpRangeFile | None = None,
) -> int:
    written = 0
    target.mkdir(parents=True, exist_ok=True)
    for info in members:
        relative = PurePosixPath(info.filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise HyLoggerError(f"Unsafe path in ZIP archive: {info.filename}")
        destination = target.joinpath(*relative.parts)
        if target.resolve() not in destination.resolve().parents:
            raise HyLoggerError(f"Unsafe extraction target: {info.filename}")
        if destination.exists() and destination.stat().st_size == info.file_size:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if remote is not None and info.compress_size >= remote.block:
            # Local header (30 bytes + name + extra) precedes the data; 64 KiB slack covers it.
            header = 30 + len(info.filename.encode()) + 65536
            remote.prefetch(info.header_offset, info.header_offset + header + info.compress_size)
        partial = destination.with_name(destination.name + ".part")
        try:
            with package.open(info) as source, partial.open("wb") as output:
                shutil.copyfileobj(source, output, length=8 << 20)  # CRC checked at EOF
        finally:
            if remote is not None:
                remote.drop_prefetch()
        partial.replace(destination)
        written += 1
    return written


def fetch_spectral(
    config: AppConfig, hole_id: str, *, local_archive: Path | None = None, block: int = 8 << 20
) -> dict[str, Any]:
    """Extract spectral members to downloads/raw/<hole>/extracted, remotely or from a local ZIP."""
    hole_id = hole_id.upper()
    config.ensure_layout()
    manifest = Manifest(config.manifest_path)
    target = config.data_dir / "downloads" / "raw" / hole_id / "extracted"
    started = time.perf_counter()
    if local_archive is not None:
        source_url, size = local_archive.resolve().as_uri(), local_archive.stat().st_size
        handle: Any = local_archive.open("rb")
        stream = None
    else:
        for attempt in range(4):
            try:
                probe = probe_raw(config, hole_id)
                break
            except requests.ConnectionError:
                if attempt == 3:
                    raise
                time.sleep(30 * (attempt + 1))
        source_url = probe["url"]
        size = probe["size_bytes"]
        if not size:
            raise HyLoggerError(f"Raw package size unknown for {hole_id}; cannot use ranges")
        stream = HttpRangeFile(
            source_url, int(size), block=block, timeout=config.request_timeout_seconds
        )
        handle = io.BufferedReader(stream, buffer_size=1 << 16)
    artifact_key = f"raw_spectral:{hole_id}"
    manifest.record(
        artifact_key=artifact_key,
        hole_id=hole_id,
        kind="raw_spectral",
        status="downloading",
        source_url=source_url,
        local_path=target,
        started_at=utc_now(),
        metadata={"archive_size_bytes": size},
    )
    try:
        with handle, zipfile.ZipFile(handle) as package:
            members = spectral_members(package)
            if not any(info.filename.lower().endswith("_tsg.tsg") for info in members):
                raise HyLoggerError(f"No NIR/SWIR TSG header in the {hole_id} package")
            written = _extract(package, members, target, stream)
        verification = verify_extracted_package(target)
    except Exception as error:
        manifest.record(
            artifact_key=artifact_key,
            hole_id=hole_id,
            kind="raw_spectral",
            status="failed",
            error=f"{type(error).__name__}: {error}",
        )
        raise
    member_bytes = sum(info.compress_size for info in members)
    result = {
        "hole_id": hole_id,
        "source": source_url,
        "archive_size_bytes": size,
        "members": [info.filename for info in members],
        "member_compressed_bytes": member_bytes,
        "bytes_transferred": stream.bytes_fetched if stream else 0,
        "range_requests": stream.requests if stream else 0,
        "files_written": written,
        "seconds": round(time.perf_counter() - started, 1),
        "verification": verification,
    }
    manifest.record(
        artifact_key=artifact_key,
        hole_id=hole_id,
        kind="raw_spectral",
        status="complete",
        source_url=source_url,
        local_path=target,
        size_bytes=member_bytes,
        completed_at=utc_now(),
        metadata=result,
    )
    return result

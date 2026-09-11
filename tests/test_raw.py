from __future__ import annotations

import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hylogger.errors import HyLoggerError
from hylogger.raw import check_raw_availability, download_raw_hole, extract_archive
from hylogger.util import atomic_write_frame


def test_extract_archive_selects_components(app_config, tmp_path: Path) -> None:
    archive = tmp_path / "TEST.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("dataset/core.jpg", b"image")
        package.writestr("dataset/TEST_tsg.tsg", b"header")
    target = tmp_path / "extracted"

    result = extract_archive(archive, target, {"images"})

    assert (target / "dataset" / "core.jpg").read_bytes() == b"image"
    assert not (target / "dataset" / "TEST_tsg.tsg").exists()
    assert result["pytsg_verified"] is False


def test_extract_archive_blocks_zip_slip(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape.jpg", b"unsafe")

    with pytest.raises(HyLoggerError, match="Unsafe path"):
        extract_archive(archive, tmp_path / "out", {"images"})


def test_raw_download_resumes_part_file(app_config) -> None:
    content = b"hylogger-data" * 10_000

    class RangeHandler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/zip")
            self.end_headers()

        def do_GET(self):
            start = 0
            if self.headers.get("Range"):
                start = int(self.headers["Range"].split("=")[1].split("-")[0])
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}"
                )
            else:
                self.send_response(200)
            body = content[start:]
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        local_config = type(app_config)(
            **{
                **app_config.__dict__,
                "raw_file_base_url": f"http://127.0.0.1:{server.server_port}/files/",
            }
        )
        part = local_config.data_dir / "downloads" / "raw" / "TEST" / "archive" / "TEST.zip.part"
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(content[:12_345])

        result = download_raw_hole(local_config, "TEST", components=set())

        assert result["resumed"] is True
        assert Path(result["archive"]).read_bytes() == content
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _write_catalog(app_config, records: list[dict]) -> None:
    import pandas as pd

    from hylogger.catalog import CATALOG_COLUMNS

    frame = pd.DataFrame(records)
    for column in CATALOG_COLUMNS:
        if column not in frame:
            frame[column] = None
    atomic_write_frame(frame[CATALOG_COLUMNS], app_config.data_dir / "catalog" / "catalog.parquet")


def test_check_raw_availability_reads_the_catalog(app_config) -> None:
    _write_catalog(
        app_config,
        [
            {
                "hole_id": "WITHRAW",
                "nvcl_available": True,
                "raw_available": True,
                "raw_size_bytes": 1024,
                "raw_url": "https://example.invalid/files/rs07/WA/WITHRAW.zip",
            },
            {"hole_id": "NORAW", "nvcl_available": True, "raw_available": False},
        ],
    )

    rows = check_raw_availability(app_config, ["withraw", "NORAW", "NEVERSEEN"])

    assert [row["status"] for row in rows] == ["available", "missing", "unknown"]
    assert rows[0]["raw_url"].endswith("WITHRAW.zip")
    assert rows[0]["raw_size_bytes"] == 1024
    assert rows[1]["processed_available"] is True
    assert rows[2]["in_catalog"] is False


def test_check_raw_availability_probe_overrides_a_stale_catalog(app_config) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            present = self.path.endswith("PRESENT.zip")
            self.send_response(200 if present else 404)
            if present:
                self.send_header("Content-Length", "4096")
            self.end_headers()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/files/"
        _write_catalog(
            app_config,
            [
                {
                    "hole_id": "GONE",
                    "nvcl_available": True,
                    "raw_available": True,
                    "raw_url": f"{base}rs07/WA/GONE.zip",
                },
                {"hole_id": "PRESENT", "nvcl_available": True, "raw_available": False},
            ],
        )
        local_config = type(app_config)(**{**app_config.__dict__, "raw_file_base_url": base})

        rows = check_raw_availability(local_config, ["GONE", "PRESENT"], probe=True)

        assert rows[0]["status"] == "missing"
        assert rows[0]["http_status"] == 404
        assert rows[1]["status"] == "available"
        assert rows[1]["raw_size_bytes"] == 4096
    finally:
        server.shutdown()
        thread.join(timeout=2)

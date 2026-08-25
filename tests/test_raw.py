from __future__ import annotations

import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hylogger.errors import HyLoggerError
from hylogger.raw import download_raw_hole, extract_archive


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

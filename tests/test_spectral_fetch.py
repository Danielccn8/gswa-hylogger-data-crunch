from __future__ import annotations

import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hylogger.explorer import spectral_fetch
from hylogger.explorer.spectral_fetch import HttpRangeFile


@pytest.fixture
def range_server(tmp_path):
    """Local server that honours explicit ranges and rejects suffix ranges, like THREDDS."""
    archive = tmp_path / "TEST.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("ds/TEST_tsg.tsg", b"header" * 2000)
        package.writestr("ds/TEST_tsg.bip", bytes(range(256)) * 40_000)
        package.writestr("ds/TEST_tsg_cras.bip", os.urandom(1_500_000))  # incompressible imagery
        package.writestr("ds/tray_01.jpg", os.urandom(400_000))
    content = archive.read_bytes()
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            spec = self.headers.get("Range", "")
            requests.append(spec)
            start, _, end = spec.removeprefix("bytes=").partition("-")
            if not start:
                self.send_response(400)
                self.end_headers()
                return
            end_index = int(end) if end else len(content) - 1
            body = content[int(start) : end_index + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end_index}/{len(content)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/TEST.zip", archive, requests
    server.shutdown()
    thread.join(timeout=2)


def test_range_file_reads_like_local_file(range_server):
    url, archive, _ = range_server
    remote = HttpRangeFile(url, archive.stat().st_size, block=4096, timeout=5, retries=0)
    with zipfile.ZipFile(remote) as package, zipfile.ZipFile(archive) as local:
        assert package.namelist() == local.namelist()
        assert package.read("ds/TEST_tsg.bip") == local.read("ds/TEST_tsg.bip")


def test_fetch_spectral_extracts_only_spectra(app_config, range_server, monkeypatch):
    url, archive, requests = range_server
    size = archive.stat().st_size
    monkeypatch.setattr(
        spectral_fetch, "probe_raw", lambda _config, _hole: {"url": url, "size_bytes": size}
    )
    monkeypatch.setattr(
        spectral_fetch, "verify_extracted_package", lambda _t: {"pytsg_verified": True}
    )
    result = spectral_fetch.fetch_spectral(app_config, "TEST", block=64 * 1024)
    target = app_config.data_dir / "downloads" / "raw" / "TEST" / "extracted" / "ds"
    with zipfile.ZipFile(archive) as local:
        assert (target / "TEST_tsg.bip").read_bytes() == local.read("ds/TEST_tsg.bip")
        assert (target / "TEST_tsg.tsg").read_bytes() == local.read("ds/TEST_tsg.tsg")
    assert not (target / "TEST_tsg_cras.bip").exists() and not (target / "tray_01.jpg").exists()
    assert result["bytes_transferred"] < size / 4  # imagery never transferred
    assert all(spec.split("=")[1][0].isdigit() for spec in requests)  # never a suffix range

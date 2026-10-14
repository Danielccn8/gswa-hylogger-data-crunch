"""Build the self-contained Windows EXE; downloads no geological data.

Run with the Explorer pinned venv plus pyinstaller==6.22.3. Runtime archives
come from EDB and OSGeo. Build manifest records URLs and SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "postgresql.zip": "https://get.enterprisedb.com/postgresql/postgresql-16.15-1-windows-x64-binaries.zip",
    "postgis.zip": "https://download.osgeo.org/postgis/windows/pg16/postgis-bundle-pg16-3.6.2x64.zip",
}


def main():
    cache = ROOT / "build" / "vendor"
    cache.mkdir(parents=True, exist_ok=True)
    files = {}
    manifest = {}
    for name, url in SOURCES.items():
        path = cache / name
        if not path.exists():
            temporary = path.with_suffix(".part")
            urllib.request.urlretrieve(url, temporary)
            temporary.replace(path)
        with path.open("rb") as handle:
            manifest[name] = {
                "url": url,
                "sha256": hashlib.file_digest(handle, "sha256").hexdigest(),
            }
        with zipfile.ZipFile(path) as package:
            for item in package.infolist():
                if item.is_dir():
                    continue
                relative = "/".join(item.filename.split("/")[1:])
                if relative.split("/")[0] in ("bin", "lib", "share") or "/" not in relative:
                    files[relative] = package.read(item)
    vendor = ROOT / "build" / "desktop-resources" / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    runtime = vendor / "postgres-runtime.zip"
    with zipfile.ZipFile(
        runtime, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as package:
        for name, content in sorted(files.items()):
            package.writestr(name, content)
    with runtime.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    (vendor / "runtime.json").write_text(
        json.dumps({"sha256": digest, "sources": manifest}, indent=2)
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            str(ROOT / "scripts" / "HyLoggerExplorer.spec"),
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()

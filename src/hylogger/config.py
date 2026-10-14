from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError

DATA_DIRECTORIES = (
    "catalog",
    "downloads/processed",
    "downloads/raw",
    "prepared/samples",
    "prepared/spectra",
    "prepared/features",
    "manifests",
    "cache",
)


def find_repo_root(start: Path | None = None) -> Path:
    import sys
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "configs").exists():
            return candidate
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "pyproject.toml").exists():
        return package_root
    raise ConfigurationError("Could not find the repository root containing pyproject.toml.")


@dataclass(frozen=True)
class AppConfig:
    repo_root: Path
    data_dir: Path
    config_path: Path
    nvcl_provider: str
    raw_catalog_url: str
    raw_file_base_url: str
    request_timeout_seconds: int
    retries: int
    chunk_size_bytes: int
    minimum_free_space_bytes: int
    profiles: dict[str, dict[str, Any]]

    @classmethod
    def load(
        cls,
        *,
        repo_root: Path | None = None,
        data_dir: Path | str | None = None,
        config_path: Path | str | None = None,
    ) -> AppConfig:
        root = (repo_root or find_repo_root()).resolve()
        path = Path(config_path) if config_path else root / "configs" / "project.toml"
        path = path.resolve()
        if not path.exists():
            raise ConfigurationError(f"Project configuration not found: {path}")

        with path.open("rb") as handle:
            raw = tomllib.load(handle)

        project = raw.get("project", {})
        services = raw.get("services", {})
        raw_settings = raw.get("raw", {})
        profile_settings = raw.get("processed", {}).get("profiles", {})

        override = data_dir or os.environ.get("HYLOGGER_DATA_DIR")
        configured = override or project.get("default_data_dir", "data")
        resolved_data = Path(configured).expanduser()
        if not resolved_data.is_absolute():
            resolved_data = root / resolved_data

        return cls(
            repo_root=root,
            data_dir=resolved_data.resolve(),
            config_path=path,
            nvcl_provider=os.environ.get(
                "HYLOGGER_NVCL_PROVIDER", services.get("nvcl_provider", "wa")
            ),
            raw_catalog_url=os.environ.get(
                "HYLOGGER_RAW_CATALOG_URL",
                services.get("raw_catalog_url", ""),
            ),
            raw_file_base_url=os.environ.get(
                "HYLOGGER_RAW_FILE_BASE_URL",
                services.get("raw_file_base_url", ""),
            ),
            request_timeout_seconds=int(services.get("request_timeout_seconds", 120)),
            retries=int(services.get("retries", 4)),
            chunk_size_bytes=int(raw_settings.get("chunk_size_bytes", 8 * 1024 * 1024)),
            minimum_free_space_bytes=int(raw_settings.get("minimum_free_space_bytes", 2 * 1024**3)),
            profiles=profile_settings,
        )

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifests" / "downloads.sqlite"

    @property
    def selections_dir(self) -> Path:
        return self.repo_root / "configs" / "selections"

    @property
    def reports_dir(self) -> Path:
        return self.repo_root / "reports" / "inspections"

    def ensure_layout(self) -> None:
        for relative in DATA_DIRECTORIES:
            (self.data_dir / relative).mkdir(parents=True, exist_ok=True)
        self.selections_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

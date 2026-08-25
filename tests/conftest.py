from __future__ import annotations

from pathlib import Path

import pytest

from hylogger.config import AppConfig


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    config = AppConfig(
        repo_root=tmp_path,
        data_dir=tmp_path / "data",
        config_path=tmp_path / "configs" / "project.toml",
        nvcl_provider="wa",
        raw_catalog_url="https://example.invalid/catalog.xml",
        raw_file_base_url="https://example.invalid/files/",
        request_timeout_seconds=5,
        retries=0,
        chunk_size_bytes=1024,
        minimum_free_space_bytes=0,
        profiles={
            "clustering": {"patterns": ["Min* *TSA*", "Grp* *TSA*", "Wt* *TSA*", "Final Mask"]}
        },
    )
    config.ensure_layout()
    return config

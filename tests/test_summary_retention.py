from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import pytest

from hylogger.errors import HyLoggerError
from hylogger.explorer import raw_summary
from hylogger.jobs.step import raw_summarize


def test_failed_second_sensor_keeps_all_sources(app_config, monkeypatch):
    package = app_config.data_dir / "downloads/raw/A/extracted"
    package.mkdir(parents=True)
    for name in ("A_tsg.tsg", "A_tsg.bip", "A_tsg_tir.tsg", "A_tsg_tir.bip"):
        (package / name).write_bytes(b"valuable source")
    result = {"sensors": {"nir": {"status": "summarised"}, "tir": {"status": "unavailable"}}}
    monkeypatch.setattr(raw_summary, "summarise_hole", lambda *args: result)
    output = app_config.data_dir / "products/raw/A"
    output.mkdir(parents=True)
    pd.DataFrame({"bin": [0]}).to_parquet(output / "raw_nir.parquet")
    np.savez(output / "raw_nir.npz", spectra=[[1, 2]], wavelengths=[450, 460], bins=[0])
    with pytest.raises(HyLoggerError, match="incomplete summary"):
        raw_summarize(app_config, "A", argparse.Namespace(discard_source=True))
    assert len(list(package.iterdir())) == 4


def test_invalid_written_product_keeps_sources(app_config, monkeypatch):
    package = app_config.data_dir / "downloads/raw/A/extracted"
    package.mkdir(parents=True)
    (package / "A_tsg.tsg").write_bytes(b"header")
    (package / "A_tsg.bip").write_bytes(b"spectra")
    output = app_config.data_dir / "products/raw/A"
    output.mkdir(parents=True)
    pd.DataFrame({"bin": [0]}).to_parquet(output / "raw_nir.parquet")
    np.savez(output / "raw_nir.npz", spectra=[[np.nan, 2]], wavelengths=[450, 460], bins=[0])
    monkeypatch.setattr(
        raw_summary, "summarise_hole", lambda *args: {"sensors": {"nir": {"status": "summarised"}}}
    )
    with pytest.raises(HyLoggerError, match="invalid persisted"):
        raw_summarize(app_config, "A", argparse.Namespace(discard_source=True))
    assert (package / "A_tsg.bip").exists()


def test_verified_cleanup_keeps_products_and_provenance(app_config, monkeypatch):
    package = app_config.data_dir / "downloads/raw/A/extracted"
    package.mkdir(parents=True)
    (package / "A_tsg.tsg").write_bytes(b"header")
    (package / "A_tsg.bip").write_bytes(b"spectra")
    output = app_config.data_dir / "products/raw/A"
    output.mkdir(parents=True)
    pd.DataFrame({"bin": [0]}).to_parquet(output / "raw_nir.parquet")
    np.savez(output / "raw_nir.npz", spectra=[[1, 2]], wavelengths=[450, 460], bins=[0])
    monkeypatch.setattr(
        raw_summary, "summarise_hole", lambda *args: {"sensors": {"nir": {"status": "summarised"}}}
    )
    result = raw_summarize(app_config, "A", argparse.Namespace(discard_source=True))
    assert result["discarded_source_bytes"] == len(b"spectra")
    assert result["source_sha256"]["extracted/A_tsg.bip"]
    assert (package / "A_tsg.tsg").exists()
    assert not (package / "A_tsg.bip").exists()
    assert (output / "raw_nir.npz").exists()

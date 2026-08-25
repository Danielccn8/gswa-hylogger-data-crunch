from pathlib import Path

import pandas as pd
import pytest

from hylogger.inspect import inspect_hole, write_html_report
from hylogger.prepare import _section_frame, prepare_hole


def _write_processed_fixture(app_config) -> Path:
    path = (
        app_config.data_dir / "downloads" / "processed" / "TEST" / "DATASET" / "scalar_logs.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "hole_id": ["TEST", "TEST", "TEST"],
            "dataset_id": ["DATASET"] * 3,
            "start_depth": [10.2, 10.1, 10.2],
            "grp1_u_tsas": ["CHLORITE", "WHITE-MICA", "CHLORITE"],
            "min1_u_tsas": ["Chlorite-FeMg", "Muscovite", "Chlorite-FeMg"],
        }
    ).to_parquet(path, index=False)
    return path


def test_prepare_processed_builds_samples_and_features(app_config) -> None:
    _write_processed_fixture(app_config)

    result = prepare_hole(app_config, "TEST")

    samples = pd.read_parquet(result["sample_paths"][0])
    features = pd.read_parquet(result["feature_path"])
    assert samples["start_depth"].tolist() == [10.1, 10.2, 10.2]
    assert features.loc[0, "processed__grp1_u_tsas__chlorite"] == pytest.approx(2 / 3)


def test_raw_section_uses_canonical_hole_id_when_source_already_has_one() -> None:
    class FakeSection:
        sampleheaders = pd.DataFrame({"D": [2.0], "HoleID": ["SOURCE"]})
        scalars = pd.DataFrame({"Min1 uTSAS": ["Muscovite"]})

    frame = _section_frame(FakeSection(), hole_id="CANONICAL", region="NIR")

    assert frame.columns[:3].tolist() == ["hole_id", "spectral_region", "depth_m"]
    assert frame.loc[0, "hole_id"] == "CANONICAL"


def test_inspector_writes_static_report(app_config) -> None:
    _write_processed_fixture(app_config)
    summary = inspect_hole(app_config, "TEST")
    report = write_html_report(app_config, summary)

    assert summary["processed"][0]["rows"] == 3
    assert "HyLogger inspection" in report.read_text(encoding="utf-8")

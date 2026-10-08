from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from hylogger.manifest import Manifest
from hylogger.processed import (
    describe_log,
    fetch_processed_hole,
    normalise_scalar_csv,
    processing_level,
    select_logs,
    spectral_region,
)


def test_jclst_logs_are_recognised_as_tir() -> None:
    assert spectral_region("Min1 ujCLST") == "TIR"
    assert spectral_region("Wt1 sjCLST") == "TIR"


def test_jclst_processing_levels_are_recognised() -> None:
    assert processing_level("Min1 sjCLST") == "L1-system"
    assert processing_level("Min1 ujCLST") == "L2-user"
    assert processing_level("Min1 djCLST") == "L3-domain"


def test_describe_log_classifies_user_jclst_log() -> None:
    result = describe_log(SimpleNamespace(log_name="Min1 ujCLST", log_type="1"))

    assert result["spectral_region"] == "TIR"
    assert result["processing_level"] == "L2-user"


def test_normalise_scalar_csv_replaces_tsg_sentinel() -> None:
    csv_content = (Path(__file__).parent / "fixtures" / "scalar_logs.csv").read_text()
    frame = normalise_scalar_csv(csv_content, hole_id="TEST", dataset_id="DATASET")

    assert frame.columns[:3].tolist() == ["hole_id", "dataset_id", "start_depth"]
    assert pd.isna(frame.loc[1, "wt1_u_tsas"])
    assert frame["hole_id"].unique().tolist() == ["TEST"]


def test_profile_selects_expected_logs(app_config) -> None:
    logs = [
        {"log_id": "1", "log_name": "Min1 uTSAS"},
        {"log_id": "2", "log_name": "Image Brightness"},
        {"log_id": "3", "log_name": "Final Mask"},
    ]
    selected = select_logs(
        logs,
        config=app_config,
        profile="clustering",
        all_logs=False,
        names=None,
    )

    assert [item["log_id"] for item in selected] == ["1", "3"]


def test_processed_fetch_writes_source_parquet_and_manifest(app_config, monkeypatch) -> None:
    csv_content = (Path(__file__).parent / "fixtures" / "scalar_logs.csv").read_text()
    inventory = {
        "hole_id": "TEST",
        "dataset_count": 1,
        "datasets": [
            {
                "dataset_id": "DATASET",
                "dataset_name": "Test data",
                "logs": [
                    {"log_id": "min", "log_name": "Min1 uTSAS"},
                    {"log_id": "mask", "log_name": "Final Mask"},
                ],
            }
        ],
    }

    def fake_download(_config, log_ids):
        assert log_ids == ["min", "mask"]
        return csv_content

    monkeypatch.setattr("hylogger.processed.download_scalars", fake_download)
    monkeypatch.setattr(
        "hylogger.processed.inventory_hole",
        lambda _config, _hole_id: inventory,
    )

    outputs = fetch_processed_hole(app_config, "TEST")

    parquet = Path(outputs[0]["path"])
    assert parquet.exists()
    assert pd.read_parquet(parquet).shape == (3, 7)
    manifest = Manifest(app_config.manifest_path).get("processed:TEST:DATASET")
    assert manifest["status"] == "complete"
    assert manifest["metadata"]["rows"] == 3


def test_narrow_fetch_keeps_broad_selected_table(app_config, monkeypatch):
    folder = app_config.data_dir / "downloads/processed/TEST/DATASET"
    folder.mkdir(parents=True)
    broad = pd.DataFrame(
        {
            "hole_id": ["TEST"],
            "dataset_id": ["DATASET"],
            "start_depth": [1.0],
            "grp1_u_tsas": ["A"],
            "grp1_uj_clst": ["B"],
        }
    )
    broad.to_parquet(folder / "scalar_logs.parquet", index=False)
    inventory = {
        "datasets": [{"dataset_id": "DATASET", "logs": [{"log_id": "x", "log_name": "Grp1 uTSAS"}]}]
    }
    monkeypatch.setattr("hylogger.processed.inventory_hole", lambda *args: inventory)
    monkeypatch.setattr(
        "hylogger.processed.download_scalars",
        lambda *args: "Start Depth,Grp1 uTSAS\n1,A\n",
    )
    output = fetch_processed_hole(app_config, "TEST")
    assert output[0]["selected"] is False
    pd.testing.assert_frame_equal(pd.read_parquet(folder / "scalar_logs.parquet"), broad)
    assert Path(output[0]["path"]).is_file()
    assert len(list((app_config.data_dir / "snapshots").glob("*/scalar_logs.parquet"))) == 2

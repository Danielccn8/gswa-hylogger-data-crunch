from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hylogger.explorer.features import MissingLogs, interval_features, select_dataset


def _write(tmp_path: Path, frame: pd.DataFrame, dataset: str = "DATASET") -> Path:
    path = tmp_path / "TEST" / dataset / "scalar_logs.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _readings() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_depth": [0.1, 0.4, 0.7, 1.2, 1.5, 1.8, -1.0, 2.2],
            "min1_u_tsas": [
                "Muscovite",
                "Muscovite",
                "Aspectral",
                "Chlorite-Mg",
                "WhiteMarker",
                "17",
                "Muscovite",
                "Calcite",
            ],
            "wt1_u_tsas": [0.6, 1.0, np.nan, 0.8, 1.0, 1.0, 1.0, 0.5],
            "grp1_u_tsas": [
                "WHITE-MICA",
                "WHITE-MICA",
                "INVALID",
                "CHLORITE",
                "NOTAROK",
                "17",
                "WHITE-MICA",
                "CARBONATE",
            ],
            "min2_u_tsas": ["Phengite", None, None, "Phengite", None, None, None, "Dolomite"],
            "wt2_u_tsas": [0.4, np.nan, np.nan, 0.2, np.nan, np.nan, np.nan, 0.5],
            "grp2_u_tsas": ["WHITE-MICA", None, None, "WHITE-MICA", None, None, None, "CARBONATE"],
            "error_u_tsas": [100, 120, np.nan, 90, np.nan, np.nan, 50, 80],
        }
    )


def test_abundances_sum_to_one_and_reasons_reconcile(tmp_path):
    table, report = interval_features(_write(tmp_path, _readings()), "TEST", "swir")
    reasons = report["primary_reasons"]
    assert sum(reasons.values()) == report["rows_received"] == 8
    assert reasons["invalid_depth"] == 1
    assert reasons["nondiagnostic"] == 1  # Aspectral is not a mineral
    assert reasons["not_rock"] == 1  # marker pen is an artefact, not geology
    assert reasons["unknown_class"] == 1  # numeric code without a dictionary
    diagnostic = table[table.n_admitted > 0]
    assert np.allclose(diagnostic.minerals.map(lambda d: sum(d.values())), 1.0)
    first = table.set_index("bin").loc[0]
    assert first.n_total == 3 and first.n_admitted == 2
    # (0.6 Muscovite + 0.4 Phengite) and (1.0 Muscovite) averaged over two readings
    assert first.minerals == pytest.approx({"Muscovite": 0.8, "Phengite": 0.2})
    assert first.groups == pytest.approx({"WHITE-MICA": 1.0})
    assert "WhiteMarker" not in report["minerals_seen"]


def test_missing_weights_fall_back_to_primary_mineral(tmp_path):
    frame = _readings()[["start_depth", "min1_u_tsas", "grp1_u_tsas"]]
    table, report = interval_features(_write(tmp_path, frame), "TEST", "swir")
    assert report["weights_available"] is False
    assert table.set_index("bin").loc[0].minerals == pytest.approx({"Muscovite": 1.0})


def test_absent_sensor_is_reported(tmp_path):
    with pytest.raises(MissingLogs):
        interval_features(_write(tmp_path, _readings()), "TEST", "tir")


def test_select_dataset_prefers_core_then_newest(tmp_path):
    hole = tmp_path / "TEST"
    for dataset in ("A", "B", "C"):
        _write(tmp_path, _readings(), dataset)
    (hole / "inventory.json").write_text(
        '{"datasets": ['
        '{"dataset_id": "A", "dataset_name": "TEST_cuttings", "modified_date": "2026"},'
        '{"dataset_id": "B", "dataset_name": "TEST", "modified_date": "2020"},'
        '{"dataset_id": "C", "dataset_name": "TEST", "modified_date": "2024"}]}',
        encoding="utf-8",
    )
    assert select_dataset(hole).parent.name == "C"

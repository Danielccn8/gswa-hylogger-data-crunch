import numpy as np
import pandas as pd
import pytest

from hylogger.errors import HyLoggerError
from hylogger.preparation import gate_readings, wavelength_grid
from hylogger.system import interval_set


def test_depths_masks_and_spectra_remain_paired():
    frame = pd.DataFrame(
        {
            "depth_m": [2, 1, 1, np.inf, -1, "-3.402823e38", 4, 5, 6],
            "final_mask": [1, 1, 1, 1, 1, 1, None, 0, 1],
        }
    )
    spectra = np.array([[20], [10], [11], [40], [50], [60], [70], [80], [-3.402823e38]])
    kept, qc = gate_readings(frame, depth_column="depth_m", spectra=spectra, require_mask=True)
    assert kept.source_row.tolist() == [1, 2, 0]
    assert spectra[kept.source_row].ravel().tolist() == [10, 11, 20]
    assert qc["primary_reasons"] == {
        "admitted": 3,
        "invalid_depth": 3,
        "invalid_spectrum": 1,
        "unknown_mask": 1,
        "masked": 1,
    }
    assert sum(qc["primary_reasons"].values()) == len(frame)


def test_missing_mask_has_explicit_source_specific_eligibility():
    frame = pd.DataFrame({"depth_m": [1, 2]})
    raw, qc = gate_readings(frame, depth_column="depth_m", require_mask=True)
    assert raw.empty and qc["primary_reasons"]["unknown_mask"] == 2
    processed, qc = gate_readings(frame, depth_column="depth_m")
    assert len(processed) == 2 and qc["eligibility"] == "mask_unverified"


def test_mismatched_grid_fails():
    for grid in ([1000, 900], [1000, np.nan], [1000]):
        with pytest.raises(HyLoggerError):
            wavelength_grid(np.ones((3, 2)), np.array(grid))


def test_processed_codes_are_not_invented_minerals_and_revision_is_stable(tmp_path):
    source = tmp_path / "scalar_logs.parquet"
    pd.DataFrame(
        {
            "start_depth": [1, 2, 3, np.inf],
            "grp1_u_tsas": ["CHLORITE", "7", "Aspectral", "WHITE-MICA"],
        }
    ).to_parquet(source)
    table, qc = interval_set(source, "TEST", "swir")
    assert qc["primary_reasons"]["unknown_class"] == 1
    assert qc["primary_reasons"]["invalid_depth"] == 1
    assert table.features.tolist() == [{"CHLORITE": 1}, {}, {}]
    assert interval_set(source, "TEST", "swir")[1]["interval_set_id"] == qc["interval_set_id"]
    assert interval_set(source, "TEST", "swir", 2)[1]["interval_set_id"] != qc["interval_set_id"]

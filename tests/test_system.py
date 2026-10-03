import numpy as np
import pandas as pd
import pytest

from hylogger.interval_model import fit, infer, matrix
from hylogger.prepare import _prepare_raw_package
from hylogger.system import interval_set
from hylogger.TIR_PROCESSING import clean_tir_holes, remove_duplicate_depths


def test_interval_qc_preserves_repeated_depth_and_reconciles(tmp_path):
    path = tmp_path / "DATASET" / "scalar_logs.parquet"
    path.parent.mkdir()
    pd.DataFrame(
        {
            "start_depth": [2, 1, 1, 3, 4, None],
            "grp1_u_tsas": ["A", "A", "B", "Aspectral", "A", "B"],
            "final_mask": [1, 1, 1, 1, None, 1],
        }
    ).to_parquet(path)
    table, qc = interval_set(path, "TEST", "swir", 1)
    assert qc["primary_reasons"] == {
        "admitted": 3,
        "invalid_depth": 1,
        "unknown_mask": 1,
        "masked": 0,
        "unknown_class": 0,
        "nondiagnostic": 1,
    }
    assert table.loc[table.depth_from_m == 1, "n_admitted"].iloc[0] == 2
    assert table.loc[table.depth_from_m == 1, "source_rows"].iloc[0] == [1, 2]
    assert table.loc[table.depth_from_m == 1, "features"].iloc[0] == {"A": 0.5, "B": 0.5}
    assert sum(table.loc[table.depth_from_m == 1, "reason_counts"].iloc[0].values()) == 2


def test_inference_rejects_unseen_group(tmp_path):
    rows = []
    for h in ("A", "B", "C"):
        for index in range(6):
            rows.append(
                {
                    "interval_id": f"{h}{index}",
                    "interval_set_id": h,
                    "hole_id": h,
                    "region": "swir",
                    "n_admitted": 4,
                    "features": {"X": 1.0} if index % 2 else {"Y": 1.0},
                }
            )
    card = fit(pd.DataFrame(rows), tmp_path)
    assert card["test_hole"] == "C"
    model_dir = tmp_path / card["model_id"]
    novel = pd.DataFrame([{**rows[0], "features": {"NEW": 1.0}}])
    with pytest.raises(ValueError, match="Unseen feature"):
        infer(novel, model_dir)
    before = (model_dir / "model.joblib").read_bytes()
    mixed = pd.DataFrame([rows[0], {**rows[1], "features": {"NEW": 1.0}}])
    flagged = infer(mixed, model_dir, flag_out_of_schema=True)
    assert flagged.status.tolist() == ["assigned", "out_of_schema"]
    assert (model_dir / "model.joblib").read_bytes() == before


def test_feature_matrix_survives_parquet_sparse_struct_roundtrip(tmp_path):
    path = tmp_path / "features.parquet"
    pd.DataFrame({"features": [{"A": 1.0}, {"B": 1.0}]}).to_parquet(path)
    restored = pd.read_parquet(path)
    assert matrix(restored, ["A", "B"]).tolist() == [[1.0, 0.0], [0.0, 1.0]]


def test_tir_duplicate_metadata_and_sentinel_spectrum():
    hole = {
        "spectra": np.array([[1.0, 2.0], [3.0, 4.0], [-3.402823e38, 1.0]]),
        "metadata": pd.DataFrame({"depth_m": [1.0, 1.0, 2.0], "Final Mask": [1, 1, 1]}),
    }
    retained = remove_duplicate_depths({"TEST": hole})
    assert len(retained["TEST"]["spectra"]) == 3
    cleaned = clean_tir_holes(retained)["TEST"]
    assert cleaned["spectra"].tolist() == [[1.0, 2.0], [3.0, 4.0]]


def test_raw_prepare_sorts_metadata_and_spectra_together(app_config, monkeypatch, tmp_path):
    class Section:
        sampleheaders = pd.DataFrame({"D": [2.0, 1.0, 3.0]})
        scalars = pd.DataFrame({"Final Mask": [1, 1, 1]})
        spectra = np.array([[20.0], [10.0], [-3.402823e38]])
        wavelength = np.array([1000.0])

    class Package:
        nir = Section()
        tir = None
        mir = None

    import pytsg.parse_tsg

    monkeypatch.setattr(pytsg.parse_tsg, "read_package", lambda *args, **kwargs: Package())
    result = _prepare_raw_package(app_config, "TEST", tmp_path)
    frame = pd.read_parquet(result["sample_paths"][0])
    with np.load(result["spectra_path"]) as arrays:
        assert arrays["nir_spectra"].ravel().tolist() == [10.0, 20.0]
    assert frame["source_row"].tolist() == [1, 0]
    assert frame["depth_m"].tolist() == [1.0, 2.0]

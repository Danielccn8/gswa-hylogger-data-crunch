import json

import numpy as np
import pandas as pd
import pytest

from hylogger.baseline import fit_baseline, load_baseline_inputs, save_baseline_run
from hylogger.cli import _prepare, build_parser
from hylogger.errors import HyLoggerError
from hylogger.manifest import Manifest
from hylogger.prepare import combine_feature_matrix, feature_file, prepare_hole
from hylogger.selections import add_holes, create_selection


def _processed(config, hole, groups, *, log="grp1_u_tsas", dataset="D1"):
    path = config.data_dir / "downloads" / "processed" / hole / dataset / "scalar_logs.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "hole_id": hole,
            "dataset_id": dataset,
            "start_depth": np.arange(len(groups), dtype=float),
            log: groups,
        }
    ).to_parquet(path, index=False)
    return path


def _pilot(config):
    profiles = {
        "A": ["CHLORITE"] * 8 + ["WHITE-MICA"] * 2 + [None, "INVALID"],
        "B": ["CHLORITE"] * 7 + ["WHITE-MICA"] * 3,
        "C": ["KAOLIN"] * 8 + ["WHITE-MICA"] * 2,
        "D": ["KAOLIN"] * 7 + ["WHITE-MICA"] * 3,
    }
    create_selection(config, "test", list(profiles))
    for hole, groups in profiles.items():
        _processed(config, hole, groups)
        prepare_hole(config, hole, source="processed")
    return combine_feature_matrix(config, list(profiles), "test", source="processed")


def test_explicit_processed_never_probes_raw(app_config, monkeypatch):
    _processed(app_config, "A", ["CHLORITE"])

    def unexpected(*args, **kwargs):
        pytest.fail("processed preparation must not inspect or read raw data")

    monkeypatch.setattr("hylogger.prepare.find_package_dir", unexpected)
    result = prepare_hole(app_config, "a", source="processed")
    assert result["source"] == "processed"
    assert Manifest(app_config.manifest_path).get("prepared:processed:A")["status"] == "complete"


def test_explicit_raw_does_not_fall_back_to_processed(app_config, monkeypatch):
    _processed(app_config, "A", ["CHLORITE"])
    monkeypatch.setattr("hylogger.prepare.find_package_dir", lambda *a: None)
    with pytest.raises(HyLoggerError, match="No extracted raw package"):
        prepare_hole(app_config, "A", source="raw")


def test_auto_retains_raw_first_rule(app_config, monkeypatch):
    _processed(app_config, "A", ["CHLORITE"])
    monkeypatch.setattr("hylogger.prepare.find_package_dir", lambda *a: app_config.data_dir)
    monkeypatch.setattr(
        "hylogger.prepare._prepare_raw_package",
        lambda *a: {"source": "raw", "sample_paths": []},
    )
    assert prepare_hole(app_config, "A")["source"] == "raw"


def test_processed_preserves_raw_features_and_legacy_output(app_config):
    _processed(app_config, "A", ["CHLORITE"])
    raw = feature_file(app_config, "A", source="raw")
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"existing raw feature file")
    legacy = raw.parent.parent / "A.parquet"
    legacy.write_bytes(b"legacy feature file")
    prepare_hole(app_config, "A", source="processed")
    assert raw.read_bytes() == b"existing raw feature file"
    assert legacy.read_bytes() == b"legacy feature file"


def test_multiple_processed_datasets_are_not_silently_pooled(app_config):
    _processed(app_config, "A", ["CHLORITE"], dataset="D1")
    _processed(app_config, "A", ["CHLORITE"], dataset="D2")
    with pytest.raises(HyLoggerError, match="double-count"):
        prepare_hole(app_config, "A", source="processed")


def test_matrix_preserves_unknowns_then_baseline_fills_observed_absence(app_config):
    path = _pilot(app_config)
    matrix = pd.read_parquet(path).set_index("hole_id")
    assert pd.isna(matrix.loc["C", "processed__grp1_u_tsas__chlorite"])
    inputs = load_baseline_inputs(app_config, "test")
    assert inputs.proportions.loc["C", "chlorite"] == 0
    assert inputs.proportions.sum(axis=1).eq(1).all()
    assert inputs.quality.loc["A", "valid_rows"] == 10
    assert inputs.quality.loc["A", "missing_rows"] == 1
    assert inputs.quality.loc["A", "invalid_rows"] == 1


@pytest.mark.parametrize("case", ["missing", "invalid", "empty"])
def test_baseline_rejects_unusable_shared_log(app_config, case):
    _pilot(app_config)
    log = "min1_u_tsas" if case == "missing" else "grp1_u_tsas"
    values = ["INVALID", "Aspectral", "not a rock"] if case == "invalid" else [None]
    _processed(app_config, "D", values, log=log)
    prepare_hole(app_config, "D", source="processed")
    combine_feature_matrix(app_config, ["A", "B", "C", "D"], "test", source="processed")
    with pytest.raises(HyLoggerError, match="missing or has no valid labels for: D"):
        load_baseline_inputs(app_config, "test")


def test_missing_hole_does_not_create_partial_matrix(app_config):
    _processed(app_config, "A", ["CHLORITE"])
    prepare_hole(app_config, "A", source="processed")
    with pytest.raises(HyLoggerError, match="Missing processed features for: B"):
        combine_feature_matrix(app_config, ["A", "B"], "test", source="processed")
    assert not feature_file(app_config, "selection_test", source="processed").exists()


def test_wrong_source_feature_row_is_rejected(app_config):
    _pilot(app_config)
    path = feature_file(app_config, "A", source="processed")
    frame = pd.read_parquet(path)
    frame["feature_source"] = "raw"
    frame.to_parquet(path, index=False)
    with pytest.raises(HyLoggerError, match="Inconsistent source"):
        combine_feature_matrix(app_config, ["A", "B"], "test", source="processed")


def test_changed_selection_requires_preparation(app_config):
    _pilot(app_config)
    add_holes(app_config, "test", ["E"])
    with pytest.raises(HyLoggerError, match="Selection or matrix changed"):
        load_baseline_inputs(app_config, "test")


def test_changed_hole_feature_requires_recombining(app_config):
    _pilot(app_config)
    _processed(app_config, "A", ["KAOLIN"])
    prepare_hole(app_config, "A", source="processed")
    with pytest.raises(HyLoggerError, match="Prepared features changed for A"):
        load_baseline_inputs(app_config, "test")


def test_failed_prepare_blocks_older_selection_matrix(app_config, monkeypatch):
    path = _pilot(app_config)
    original = path.read_bytes()

    def fail(*args, **kwargs):
        raise HyLoggerError("simulated preparation failure")

    monkeypatch.setattr("hylogger.cli.prepare_hole", fail)
    args = build_parser().parse_args(["prepare", "--selection", "test", "--source", "processed"])
    with pytest.raises(HyLoggerError, match="no selection matrix was updated"):
        _prepare(app_config, args)
    assert path.read_bytes() == original
    with pytest.raises(HyLoggerError, match="last selection preparation did not complete"):
        load_baseline_inputs(app_config, "test")


@pytest.mark.parametrize("pca_variance", [None, 0.95])
def test_baseline_fits_deterministically_and_exports(app_config, pca_variance):
    _pilot(app_config)
    inputs = load_baseline_inputs(app_config, "test")
    first = fit_baseline(inputs, pca_variance=pca_variance)
    second = fit_baseline(inputs, pca_variance=pca_variance)
    assert first.assignments.equals(second.assignments)
    assert first.metrics["n_holes"] == 4
    assert first.metrics["silhouette_score"] > 0
    assert ("pca" in first.model.named_steps) == (pca_variance is not None)
    assert not any("qc__" in col or "min1" in col for col in first.model_columns)
    run = save_baseline_run(app_config, inputs, first, selection="test")
    assert pd.read_csv(run / "assignments.csv").shape == (4, 2)
    metadata = json.loads((run / "run.json").read_text())
    assert metadata["source"] == "processed"
    assert metadata["selection"] == "test"
    assert metadata["model_columns"] == first.model_columns


@pytest.mark.parametrize("n_clusters", [1, 4, 5])
def test_bad_k_reports_clear_error_instead_of_silhouette_crash(app_config, n_clusters):
    _pilot(app_config)
    with pytest.raises(HyLoggerError, match="N_CLUSTERS"):
        fit_baseline(load_baseline_inputs(app_config, "test"), n_clusters=n_clusters)


def test_identical_profiles_are_rejected(app_config):
    _pilot(app_config)
    inputs = load_baseline_inputs(app_config, "test")
    inputs.proportions.loc[:, :] = inputs.proportions.iloc[0].to_numpy()
    with pytest.raises(HyLoggerError, match="Too few distinct hole profiles"):
        fit_baseline(inputs)


def test_single_hole_is_not_between_hole_clustering(app_config):
    _pilot(app_config)
    inputs = load_baseline_inputs(app_config, "test")
    inputs.proportions = inputs.proportions.iloc[:1]
    with pytest.raises(HyLoggerError, match="single hole"):
        fit_baseline(inputs)


def test_inspect_reports_source_specific_preparation(app_config):
    from hylogger.inspect import inspect_hole

    _processed(app_config, "A", ["CHLORITE"])
    prepare_hole(app_config, "A", source="processed")
    summary = inspect_hole(app_config, "A")
    assert summary["prepared"]["source"] == "processed"
    assert set(summary["prepared_sources"]) == {"processed"}


def test_auto_mixed_source_selection_is_rejected(app_config, monkeypatch):
    create_selection(app_config, "mixed", ["A", "B"])
    monkeypatch.setattr(
        "hylogger.cli.prepare_hole",
        lambda config, hole, **kw: {
            "source": "raw" if hole == "A" else "processed",
            "feature_path": "test",
        },
    )
    args = build_parser().parse_args(["prepare", "--selection", "mixed"])
    with pytest.raises(HyLoggerError, match="mixture of raw and processed"):
        _prepare(app_config, args)
    for source in ("raw", "processed"):
        assert not feature_file(app_config, "selection_mixed", source=source).exists()


def test_empty_selection_has_clear_error(app_config):
    create_selection(app_config, "empty", [])
    args = build_parser().parse_args(["prepare", "--selection", "empty", "--source", "processed"])
    with pytest.raises(HyLoggerError, match="selection is empty"):
        _prepare(app_config, args)


def test_successful_retry_unblocks_selection(app_config):
    path = _pilot(app_config)
    path.with_suffix(".status.json").write_text('{"status": "preparing"}')
    args = build_parser().parse_args(["prepare", "--selection", "TEST", "--source", "processed"])
    assert _prepare(app_config, args) == 0
    assert len(load_baseline_inputs(app_config, "test").proportions) == 4


def test_normalised_duplicate_hole_ids_are_rejected(app_config):
    with pytest.raises(HyLoggerError, match="distinct hole IDs"):
        combine_feature_matrix(app_config, ["a", "A"], "test", source="processed")

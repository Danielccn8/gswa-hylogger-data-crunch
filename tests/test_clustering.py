import numpy as np
import pandas as pd

from hylogger.clustering import HoleSpectra, _normalise_scalar_name, cluster_holes


def _hole(hole_id: str, offset: float, wavelengths: np.ndarray) -> HoleSpectra:
    rng = np.random.default_rng(sum(map(ord, hole_id)))
    base = np.sin(wavelengths / 80) + offset
    spectra = base + rng.normal(0, 0.05, size=(20, len(wavelengths)))
    metadata = pd.DataFrame(
        {
            "hole_id": hole_id,
            "depth_m": np.arange(20, dtype=float),
            "tray": 1,
            "source_row": np.arange(20),
        }
    )
    return HoleSpectra(hole_id, wavelengths, spectra.astype(np.float32), metadata)


def test_cluster_holes_preserves_hole_provenance_and_resamples():
    first = _hole("HOLE_A", 0.0, np.linspace(1000, 2500, 60))
    second = _hole("HOLE_B", 0.2, np.linspace(950, 2550, 65))

    result = cluster_holes(
        [first, second], n_clusters=2, training_sample_size=30, random_state=7
    )

    assert len(result.assignments) == 40
    assert set(result.assignments["hole_id"]) == {"HOLE_A", "HOLE_B"}
    assert set(result.assignments["cluster_id"]) <= {0, 1}
    assert result.cluster_sizes.groupby("hole_id")["percentage"].sum().round().eq(100).all()
    assert result.mean_snv_spectra.shape == (2, 60)
    assert result.k_scores.loc[0, "selected"]
    assert result.k_scores.loc[0, "passes_quality_checks"]


def test_scalar_names_match_space_and_underscore_variants():
    assert _normalise_scalar_name("Min1 sTSAS") == _normalise_scalar_name("Min1_sTSAS")
    assert _normalise_scalar_name("Grp1 uTSAS") == _normalise_scalar_name("Grp1_uTSAS")

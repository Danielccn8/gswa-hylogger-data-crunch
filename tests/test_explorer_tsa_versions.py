from __future__ import annotations

import numpy as np
import pandas as pd

from hylogger.explorer import tsa_versions


def _frame() -> pd.DataFrame:
    # Two metres: the first agrees fully, the second has the user swap the first mineral.
    return pd.DataFrame(
        {
            "bin": [0, 0, 1, 1],
            "min1_s_tsas": ["Muscovite", "Muscovite", "Chlorite", "Chlorite"],
            "wt1_s_tsas": [1.0, 0.6, 1.0, 1.0],
            "min2_s_tsas": [None, "Kaolinite", None, None],
            "wt2_s_tsas": [np.nan, 0.4, np.nan, np.nan],
            "min1_u_tsas": ["Muscovite", "Muscovite", "Biotite", "Chlorite"],
            "wt1_u_tsas": [1.0, 0.6, 1.0, 1.0],
            "min2_u_tsas": [None, "Kaolinite", None, None],
            "wt2_u_tsas": [np.nan, 0.4, np.nan, np.nan],
            "grp1_s_tsas": ["WHITE-MICA", "WHITE-MICA", "CHLORITE", "CHLORITE"],
            "grp1_u_tsas": ["WHITE-MICA", "WHITE-MICA", "DARK-MICA", "CHLORITE"],
        }
    )


def test_per_metre_compares_system_and_user_versions():
    table = tsa_versions.per_metre(_frame(), "nir").set_index("bin")
    assert list(table["product"].unique()) == ["swir"]  # no VNIR columns in this frame
    first, second = table.loc[0], table.loc[1]
    assert first.min1_agreement == 1.0 and first.mix_overlap == 1.0
    assert first.system_top == "Muscovite" and first.system_top_share == 0.8
    assert second.min1_agreement == 0.5 and second.mix_overlap == 0.5
    assert second.system_top == "Chlorite"


def test_summary_counts_main_mineral_changes():
    frame = _frame()
    frame.loc[3, "min1_u_tsas"] = "Biotite"
    summary = tsa_versions.summary(tsa_versions.per_metre(frame, "nir"))["swir"]
    assert summary["metres"] == 2 and summary["metres_main_mineral_changed"] == 1
    assert summary["top_changes"] == [["Chlorite", "Biotite", 1]]


def test_read_section_maps_class_zero_and_unknown_codes(monkeypatch):
    from pytsg import parse_tsg

    from hylogger.explorer.raw_summary import read_section

    nodata = np.finfo("float32").min
    scalars = np.array([[0, 2, 0.5], [1, 0, 0.7], [7, 1, 0.9], [nodata, 0, 0.1]], np.float32)
    data = np.stack([np.ones_like(scalars), scalars])
    info = {
        "coordinates": {},
        "wavelength specs": {},
        "sample headers": pd.DataFrame({"D": [1.0, 2.0, 3.0, 4.0]}),
        "class": {
            0: parse_tsg.ClassHeaders(0, "system", 2, {0: "Muscovite", 1: "Chlorite"}),
            1: parse_tsg.ClassHeaders(1, "user", 3, {0: "Kaolinite", 1: "Muscovite", 2: "Talc"}),
        },
        "band headers": [
            parse_tsg.BandHeaders(0, "Min1 sTSAS", 0, 2),
            parse_tsg.BandHeaders(1, "Min1 uTSAS", 1, 2),
            parse_tsg.BandHeaders(2, "Wt1 sTSAS", 0, 0),
        ],
    }
    monkeypatch.setattr(parse_tsg, "_read_tsg_file", lambda path: "")
    monkeypatch.setattr(parse_tsg, "_find_header_sections", lambda text: {})
    monkeypatch.setattr(parse_tsg, "_parse_tsg", lambda text, headers: info)
    monkeypatch.setattr(parse_tsg, "_read_bip", lambda path, coordinates: data)
    monkeypatch.setattr(
        parse_tsg, "_calculate_wavelengths", lambda specs, coordinates: np.arange(3)
    )
    section = read_section("x.tsg", "x.bip", "nir")
    # class table 0 is mapped (pytsg leaves it numeric); an unknown code does not raise
    assert section.scalars["Min1 sTSAS"].tolist() == ["Muscovite", "Chlorite", "class code 7", ""]
    assert section.scalars["Min1 uTSAS"].tolist() == ["Talc", "Kaolinite", "Muscovite", "Kaolinite"]
    assert np.allclose(section.scalars["Wt1 sTSAS"], [0.5, 0.7, 0.9, 0.1])

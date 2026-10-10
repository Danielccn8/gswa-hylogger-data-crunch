from __future__ import annotations

from hylogger.explorer import explain


def test_overlap_is_sum_of_minima_with_largest_parts_and_differences():
    a = {"Chlorite": 0.6, "Paragonite": 0.3, "Phengite": 0.1}
    b = {"Chlorite": 0.4, "Paragonite": 0.3, "Kaolinite": 0.3}
    result = explain.overlap(a, b)
    assert result["overlap"] == 0.7
    assert result["shared"] == [["Chlorite", 0.4], ["Paragonite", 0.3]]
    assert result["differences"][0] == ["Kaolinite", 0.0, 0.3]
    assert explain.overlap(a, a)["overlap"] == 1.0


def test_sequence_summary_keeps_order_and_weights_same_share_by_metres():
    zones = [
        {
            "a_from": 0,
            "a_to": 50,
            "cluster_id": 1,
            "cluster_name": "Chlorite",
            "same_cluster_share": 1.0,
        },
        {
            "a_from": 50,
            "a_to": 55,
            "cluster_id": 2,
            "cluster_name": "Talc",
            "same_cluster_share": 1.0,
        },
        {
            "a_from": 55,
            "a_to": 100,
            "cluster_id": 3,
            "cluster_name": "Paragonite",
            "same_cluster_share": 0.6,
        },
        {
            "a_from": 100,
            "a_to": 150,
            "cluster_id": 1,
            "cluster_name": "Chlorite",
            "same_cluster_share": 0.0,
        },
    ]
    summary = explain.sequence_summary(zones)
    assert summary["order"] == ["Chlorite", "Paragonite"]  # short zone and mismatched zone dropped
    assert summary["same_share"] == round((50 + 5 + 45 * 0.6) / 150, 3)
    line = explain.sequence_line(summary)
    assert line.startswith("Same order down the hole: Chlorite → Paragonite")


def test_lines_read_naturally():
    clusters = explain.overlap({"A-dominant": 0.8, "B": 0.2}, {"A-dominant": 0.7, "B": 0.3})
    minerals = explain.overlap({"Muscovite": 0.5}, {"Muscovite": 0.2})
    lines = explain.mix_lines("SWIR", clusters, minerals)
    assert lines[0] == "SWIR: 90% of metres in the same clusters (A-dominant 70%, B 20%)"
    assert lines[1] == "SWIR differs in Muscovite 50% vs 20%"
    raw = explain.raw_lines("NIR", None, {"aloh_2200_pos": 2196.2}, {"aloh_2200_pos": 2208.9})
    assert raw == ["Al-OH absorption 2196 vs 2209 nm (different white-mica chemistry)"]
    assert explain.context_line(12.4, True) == "12 km apart · same district"

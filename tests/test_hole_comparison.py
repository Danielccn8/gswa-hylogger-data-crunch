import pandas as pd
import pytest

from hylogger.hole_comparison import compare_holes


def _interval(hole, region, number, feature, admitted=10):
    return {
        "hole_id": hole,
        "region": region,
        "interval_set_id": f"{hole}-{region}",
        "n_admitted": admitted,
        "n_total": max(20, admitted),
        "features": feature,
        "depth_from_m": number,
    }


def test_hole_comparison_equal_interval_weight_and_missing_sensor():
    # A has two opposing intervals with very different reading counts. Its
    # whole-hole frequency should be 50/50, not weighted by scanner density.
    records = [
        _interval("A", "swir", 0, {"X": 1.0}, 100),
        _interval("A", "swir", 1, {"Y": 1.0}, 1),
        _interval("A", "swir", 2, {"X": 1.0}, 0),
        _interval("B", "swir", 0, {"X": 1.0}),
        _interval("C", "swir", 0, {"X": 0.9, "Y": 0.1}),
        _interval("D", "swir", 0, {"Y": 1.0}),
        _interval("E", "swir", 0, {"X": 0.1, "Y": 0.9}),
        _interval("F", "tir", 0, {"Z": 1.0}),
    ]
    result = compare_holes(pd.DataFrame(records), requested_holes=list("ABCDEF"), region="swir")
    a = next(row for row in result["holes"] if row["hole_id"] == "A")
    assert a["group_frequencies"] == {"X": 0.5, "Y": 0.5}
    assert a["intervals_total"] == 3
    assert a["intervals_with_features"] == 2
    assert result["excluded_holes"] == ["F"]
    assert result["included_holes"] == 5
    assert len([row for row in result["holes"] if row["review_candidate"]]) == 1


def test_hole_comparison_rejects_mixed_revisions():
    records = [_interval(hole, "swir", 0, {"X": value, "Y": 1 - value})
               for hole, value in zip("ABCD", (1, 0.9, 0.1, 0), strict=True)]
    duplicate = dict(records[0], interval_set_id="different")
    with pytest.raises(ValueError, match="Multiple interval-set revisions"):
        compare_holes(
            pd.DataFrame([*records, duplicate]), requested_holes=list("ABCD"), region="swir"
        )

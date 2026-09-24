"""Row-level quality gating applied between preparation and feature building.

TSG publishes its own usability verdict per reading (`Final Mask`) and a
confidence pair per interpretation (`Wt1 ...`, `Error ...`). Neither was
previously consulted, so every reading reached the feature matrix with equal
standing. This module makes those judgements explicit and reproducible.

Two different actions are deliberately kept apart:

* An excluded row is dropped. TSG decided the reading itself is unusable.
* A low-confidence label is nulled, not dropped. The measurement is still a
  valid observation; only the interpretation is weak. Nulling moves it to
  `missing_rows` in quality control and out of the proportion denominator,
  which preserves the distinction the data model requires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .errors import HyLoggerError

MASK_COLUMNS = ("final_mask", "finalmask", "mask")
_WEIGHT_PATTERN = re.compile(r"^wt_?(?P<rank>[123])(?:_(?P<method>.+))?$")
_ERROR_PATTERN = re.compile(r"^error(?:_(?P<method>.+))?$")
_LABEL_PATTERN = re.compile(r"^(?:min|mineral|grp|group)_?(?P<rank>[123])(?:_(?P<method>.+))?$")


@dataclass(frozen=True)
class QualityPolicy:
    """How to gate readings before they become features.

    `apply_final_mask` drops readings TSG marked unusable. This is on by
    default because a masked reading carries no interpretation to lose.

    `min_weight` and `max_error` change which labels count and therefore
    change results, so both are off by default and must be requested
    explicitly. Error scales differ between algorithms and are not
    comparable, so a single `max_error` is a blunt instrument across methods.
    """

    apply_final_mask: bool = True
    min_weight: float | None = None
    max_error: float | None = None

    def __post_init__(self) -> None:
        if self.min_weight is not None and not 0 <= self.min_weight <= 1:
            raise HyLoggerError("min_weight is a model weight and must be between 0 and 1.")
        if self.max_error is not None and self.max_error < 0:
            raise HyLoggerError("max_error must not be negative.")

    @property
    def is_default(self) -> bool:
        return self.apply_final_mask and self.min_weight is None and self.max_error is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "apply_final_mask": self.apply_final_mask,
            "min_weight": self.min_weight,
            "max_error": self.max_error,
        }


def apply_quality_policy(
    frame: pd.DataFrame, policy: QualityPolicy
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return the gated frame and a report of everything the policy changed.

    The report is written to quality control output so a run can always be
    explained without re-reading the source package.
    """
    report: dict[str, Any] = {
        "policy": policy.as_dict(),
        "rows_in": len(frame),
        "mask_column": None,
        "rows_dropped_final_mask": 0,
        "rows_unknown_final_mask": 0,
        "labels_nulled": {},
    }
    if frame.empty:
        report["rows_out"] = 0
        return frame, report

    frame = frame.copy()
    if policy.apply_final_mask:
        frame = _drop_masked_rows(frame, report)
    if policy.min_weight is not None or policy.max_error is not None:
        _null_low_confidence_labels(frame, policy, report)

    report["rows_out"] = len(frame)
    return frame.reset_index(drop=True), report


def _drop_masked_rows(frame: pd.DataFrame, report: dict[str, Any]) -> pd.DataFrame:
    column = next((name for name in MASK_COLUMNS if name in frame.columns), None)
    if column is None:
        return frame
    report["mask_column"] = column
    values = pd.to_numeric(frame[column], errors="coerce")
    # An unknown verdict is not evidence of usable rock. The processed API
    # omits this log entirely, in which case the report records that limitation.
    excluded = values.ne(1)
    report["rows_dropped_final_mask"] = int(excluded.sum())
    report["rows_unknown_final_mask"] = int(values.isna().sum())
    return frame.loc[~excluded]


def _null_low_confidence_labels(
    frame: pd.DataFrame, policy: QualityPolicy, report: dict[str, Any]
) -> None:
    labels = _labels_by_method(frame.columns)
    nulled: dict[str, int] = {}

    for column in frame.columns:
        weight = _WEIGHT_PATTERN.match(column)
        if weight and policy.min_weight is not None:
            values = pd.to_numeric(frame[column], errors="coerce")
            # A missing weight is not evidence of low confidence.
            _null_where(frame, values.lt(policy.min_weight), labels, weight, nulled)
            continue
        error = _ERROR_PATTERN.match(column)
        if error and policy.max_error is not None:
            values = pd.to_numeric(frame[column], errors="coerce")
            _null_where(frame, values.gt(policy.max_error), labels, error, nulled)

    report["labels_nulled"] = dict(sorted(nulled.items()))


def _null_where(
    frame: pd.DataFrame,
    failing: pd.Series,
    labels: dict[str | None, list[str]],
    match: re.Match[str],
    nulled: dict[str, int],
) -> None:
    if not failing.any():
        return
    # A weight ranks one interpretation; an error describes the whole fit.
    # Both are scoped to their own method, never across methods.
    ranks = {match.group("rank")} if "rank" in match.groupdict() else None
    for column in labels.get(match.group("method"), []):
        label = _LABEL_PATTERN.match(column)
        if ranks and label and label.group("rank") not in ranks:
            continue
        affected = failing & frame[column].notna()
        if not affected.any():
            continue
        frame.loc[affected, column] = None
        nulled[column] = nulled.get(column, 0) + int(affected.sum())


def _labels_by_method(columns: pd.Index) -> dict[str | None, list[str]]:
    grouped: dict[str | None, list[str]] = {}
    for column in columns:
        match = _LABEL_PATTERN.match(column)
        if match:
            grouped.setdefault(match.group("method"), []).append(column)
    return grouped

"""Prepare the paired sTSAS and uTSAS tables for later clustering.

The script performs thirteen preparation steps in order:
1. Validate that every paired drill-hole CSV has the expected structure.
2. Use each raw TSG package's own class tables to translate numeric Min/Grp
   codes into their mineral or group names.
3. Replace the raw TSG no-data number with an empty CSV cell.
4. Check whether each Min, Grp and Wt position is populated consistently.
5. Compare the sTSAS and uTSAS interpretations at the same sample rows.
6. Export the rows where both methods report different primary minerals.
7. Compare the complete Min1-Min3 mineral combinations from both methods.
8. Compare the complete Grp1-Grp3 mineral-group combinations.
9. Validate the numerical weight and error values from both methods.
10. Count rows whose complete cleaned records are exact duplicates.
11. Check whether no-data conversion created duplicates and whether duplicate
    copies contain useful TSAS results.
12. Check repeated depths and whether their mineral results differ.
13. Check for jumps in the depth sequence that may represent missing samples.

Each step writes to a new folder. The extracted source files are never changed,
and no drill-hole rows are removed during these steps.
"""

from __future__ import annotations

import csv
import filecmp
import math
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import numpy as np


# Every paired drill-hole CSV must contain these identification and depth fields.
IDENTITY_COLUMNS = ["hole_id", "start_depth", "end_depth"]

# These are the ten required fields for each interpretation family.
STSAS_COLUMNS = [
    "min1_s_tsas", "min2_s_tsas", "min3_s_tsas",
    "grp1_s_tsas", "grp2_s_tsas", "grp3_s_tsas",
    "wt1_s_tsas", "wt2_s_tsas", "wt3_s_tsas", "error_s_tsas",
]
UTSAS_COLUMNS = [
    "min1_u_tsas", "min2_u_tsas", "min3_u_tsas",
    "grp1_u_tsas", "grp2_u_tsas", "grp3_u_tsas",
    "wt1_u_tsas", "wt2_u_tsas", "wt3_u_tsas", "error_u_tsas",
]
REQUIRED_COLUMNS = IDENTITY_COLUMNS + STSAS_COLUMNS + UTSAS_COLUMNS

# TSG uses a very large negative number (approximately -3.4028233e+38) when a
# scalar has no result. It is a machine no-data marker, not a mineral code and
# not a real weight or error measurement.
RAW_NO_DATA_LIMIT = -1e38

# Mineral and group fields may contain raw numeric category codes. Step 1 counts
# those cells so they can be reviewed later; it does not translate the codes.
CATEGORY_COLUMNS = [
    column
    for column in STSAS_COLUMNS + UTSAS_COLUMNS
    if column.startswith(("min", "grp"))
]

# Connect each standard CSV column to its original name in the raw TSG file.
# This is needed because the TSG band header says which class table belongs to
# each Min or Grp column.
TSG_CATEGORY_NAMES = {
    "min1_s_tsas": "Min1 sTSAS",
    "min2_s_tsas": "Min2 sTSAS",
    "min3_s_tsas": "Min3 sTSAS",
    "grp1_s_tsas": "Grp1 sTSAS",
    "grp2_s_tsas": "Grp2 sTSAS",
    "grp3_s_tsas": "Grp3 sTSAS",
    "min1_u_tsas": "Min1 uTSAS",
    "min2_u_tsas": "Min2 uTSAS",
    "min3_u_tsas": "Min3 uTSAS",
    "grp1_u_tsas": "Grp1 uTSAS",
    "grp2_u_tsas": "Grp2 uTSAS",
    "grp3_u_tsas": "Grp3 uTSAS",
}


def write_report_csv(frame: pd.DataFrame, report_file: Path) -> None:
    """Write a report safely when an identical copy is open in Excel.

    Windows prevents replacing an open CSV. We first write the new report to a
    temporary file. If the destination is locked, we reuse it only when it is
    byte-for-byte identical to the newly calculated report. A changed report
    still raises an error so an old result can never be mistaken for a new one.
    """

    temporary_report = report_file.with_suffix(".csv.tmp")
    frame.to_csv(temporary_report, index=False)
    try:
        temporary_report.replace(report_file)
    except PermissionError:
        if report_file.is_file() and filecmp.cmp(
            temporary_report, report_file, shallow=False
        ):
            temporary_report.unlink()
            print(f"Open report is unchanged and remains valid: {report_file}")
        else:
            raise


def read_hole_ids(selection_file: Path) -> list[str]:
    """Read the 20 requested drill-hole IDs from the selection file."""

    text = selection_file.read_text(encoding="utf-8")
    hole_ids = re.findall(r"^-\s+(.+?)\s*$", text, flags=re.MULTILINE)
    if not hole_ids:
        raise ValueError(f"No drill holes were found in {selection_file}")
    return [hole_id.strip().upper() for hole_id in hole_ids]


def validate_file(
    hole_id: str,
    input_file: Path,
    expected_rows: int | None,
) -> dict[str, object]:
    """Check one paired CSV without changing its contents."""

    result: dict[str, object] = {
        "hole_id": hole_id,
        "input_file": str(input_file),
        "file_exists": input_file.is_file(),
        "filename_matches_hole": input_file.name == f"{hole_id}_stsas_utsas.csv",
        "required_columns_present": False,
        "missing_columns": "",
        "actual_rows": 0,
        "extraction_report_rows": expected_rows,
        "row_count_matches_extraction_report": False,
        "hole_id_matches_every_row": False,
        "missing_or_invalid_depth_rows": 0,
        "start_end_depth_mismatch_rows": 0,
        "numeric_category_code_cells": 0,
        "raw_no_data_marker_cells": 0,
        "validation_passed": False,
    }

    if not input_file.is_file():
        result["missing_columns"] = "FILE MISSING"
        return result

    header = pd.read_csv(input_file, nrows=0).columns.tolist()
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in header]
    result["missing_columns"] = "; ".join(missing_columns)
    result["required_columns_present"] = not missing_columns
    if missing_columns:
        return result

    wrong_hole_rows = 0
    for chunk in pd.read_csv(input_file, chunksize=100_000, low_memory=False):
        result["actual_rows"] += len(chunk)
        wrong_hole_rows += int(
            chunk["hole_id"].astype("string").str.strip().str.upper().ne(hole_id).sum()
        )

        start_depth = pd.to_numeric(chunk["start_depth"], errors="coerce")
        end_depth = pd.to_numeric(chunk["end_depth"], errors="coerce")
        result["missing_or_invalid_depth_rows"] += int(
            (start_depth.isna() | end_depth.isna()).sum()
        )
        result["start_end_depth_mismatch_rows"] += int(
            (start_depth.notna() & end_depth.notna() & start_depth.ne(end_depth)).sum()
        )

        for column in CATEGORY_COLUMNS:
            numeric = pd.to_numeric(chunk[column], errors="coerce")
            result["numeric_category_code_cells"] += int(
                (numeric.notna() & numeric.gt(-1e38)).sum()
            )
            result["raw_no_data_marker_cells"] += int(numeric.le(-1e38).sum())

    result["hole_id_matches_every_row"] = wrong_hole_rows == 0
    result["row_count_matches_extraction_report"] = (
        expected_rows is not None and result["actual_rows"] == expected_rows
    )
    result["validation_passed"] = all(
        [
            result["file_exists"],
            result["filename_matches_hole"],
            result["required_columns_present"],
            result["row_count_matches_extraction_report"],
            result["hole_id_matches_every_row"],
            result["missing_or_invalid_depth_rows"] == 0,
            result["start_end_depth_mismatch_rows"] == 0,
        ]
    )
    return result


def find_swir_tsg_file(package_folder: Path) -> Path:
    """Find the SWIR TSG header and exclude the separate TIR header."""

    # A raw package normally contains one ``*_tsg.tsg`` SWIR header and one
    # ``*_tsg_tir.tsg`` TIR header. This workflow needs the SWIR file only.
    candidates = sorted(
        path
        for path in package_folder.glob("*_tsg.tsg")
        if not path.name.lower().endswith("_tsg_tir.tsg")
    )
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one SWIR TSG header in {package_folder}, "
            f"but found {len(candidates)}"
        )
    return candidates[0]


def read_tsg_category_lookups(
    tsg_file: Path,
) -> dict[str, dict[str, object]]:
    """Read the class lookup assigned to every required Min/Grp scalar.

    A TSG band header connects a scalar such as ``Min1 sTSAS`` to a numbered
    class table. The class table then provides definitions such as
    ``17:Chlorite-FeMg`` and ``22:Hornblende``.
    """

    # TSG headers are text files. ``errors='replace'`` prevents an unrelated
    # legacy character from stopping the lookup-table scan.
    lines = tsg_file.read_text(encoding="utf-8", errors="replace").splitlines()

    # ``band_class_numbers`` will connect a TSG scalar name to a class number.
    # ``class_tables`` will hold the name and code labels for each class number.
    band_class_numbers: dict[str, int] = {}
    class_tables: dict[int, dict[str, object]] = {}
    current_section = ""
    current_class_number: int | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Section headings look like ``[band headers]`` or ``[class 0]``.
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip().lower()
            class_match = re.fullmatch(r"class\s+(\d+)", current_section)
            current_class_number = (
                int(class_match.group(1)) if class_match else None
            )
            if current_class_number is not None:
                class_tables.setdefault(
                    current_class_number,
                    {"class_name": "", "labels": {}},
                )
            continue

        if current_section == "band headers" and ":" in line:
            # A band line starts with its band number, followed by semicolon-
            # separated metadata. Position 3 is its flag and position 4 is the
            # class-table number used for category labels.
            _, band_information = line.split(":", 1)
            parts = band_information.split(";")
            if len(parts) >= 5:
                scalar_name = parts[0].strip()
                try:
                    flag = int(parts[3])
                    class_number = int(parts[4])
                except ValueError:
                    continue
                if flag == 2:
                    band_class_numbers[scalar_name] = class_number
            continue

        if current_class_number is not None:
            class_table = class_tables[current_class_number]

            # The ``name =`` line identifies the purpose of the class table,
            # for example ``S_SWIR_TSA705 Minerals``.
            if line.lower().startswith("name") and "=" in line:
                class_table["class_name"] = line.split("=", 1)[1].strip()
                continue

            # Remaining entries such as ``22:Hornblende`` define one code.
            if ":" in line:
                code_text, label = line.split(":", 1)
                try:
                    code = int(code_text.strip())
                except ValueError:
                    continue
                labels = class_table["labels"]
                if isinstance(labels, dict):
                    labels[code] = label.strip()

    # Build one verified lookup description for each standard CSV column.
    lookups: dict[str, dict[str, object]] = {}
    for csv_column, tsg_scalar_name in TSG_CATEGORY_NAMES.items():
        if tsg_scalar_name not in band_class_numbers:
            raise ValueError(
                f"{tsg_file}: no class number found for {tsg_scalar_name}"
            )
        class_number = band_class_numbers[tsg_scalar_name]
        if class_number not in class_tables:
            raise ValueError(
                f"{tsg_file}: class table {class_number} was not found"
            )
        lookups[csv_column] = {
            "tsg_scalar_name": tsg_scalar_name,
            "class_number": class_number,
            "class_name": class_tables[class_number]["class_name"],
            "labels": class_tables[class_number]["labels"],
        }
    return lookups


def resolve_file_category_codes(
    hole_id: str,
    input_file: Path,
    output_file: Path,
    tsg_file: Path,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Create one code-resolved copy and return summary and lookup reports."""

    # Read the lookup definitions from the same raw TSG package that produced
    # this drill hole's CSV. This prevents one hole from using another hole's
    # class-table assignment.
    lookups = read_tsg_category_lookups(tsg_file)

    # Count every code/name/column combination so the final mapping can be
    # reviewed instead of silently changing values.
    mapping_counts: Counter[tuple[str, int, str, int, str, str]] = Counter()
    total_rows = 0
    numeric_codes_before = 0
    resolved_codes = 0
    unresolved_codes = 0

    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_file.with_suffix(".csv.tmp")

    # The standard-library CSV reader preserves the exact text in all fields
    # that are not category codes, including depths, weights and errors.
    with (
        input_file.open("r", encoding="utf-8", newline="") as source,
        temporary_output.open("w", encoding="utf-8", newline="") as destination,
    ):
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"{input_file}: CSV header is missing")
        writer = csv.DictWriter(destination, fieldnames=reader.fieldnames)
        writer.writeheader()

        for row in reader:
            total_rows += 1
            for column in CATEGORY_COLUMNS:
                raw_value = row[column].strip()
                try:
                    numeric_value = float(raw_value)
                except (TypeError, ValueError):
                    # Readable names and blank cells do not need translation.
                    continue

                # Preserve non-finite values and the raw TSG no-data marker.
                # The no-data marker will be handled in the next cleaning step.
                if not math.isfinite(numeric_value) or numeric_value <= RAW_NO_DATA_LIMIT:
                    continue

                numeric_codes_before += 1
                lookup = lookups[column]
                labels = lookup["labels"]

                # TSG category codes must be whole numbers. A decimal numeric
                # value that is not an integer is reported as unresolved.
                code = int(numeric_value)
                label = (
                    labels.get(code)
                    if numeric_value.is_integer() and isinstance(labels, dict)
                    else None
                )

                if label:
                    row[column] = str(label)
                    status = "resolved"
                    resolved_codes += 1
                else:
                    status = "unresolved"
                    label = ""
                    unresolved_codes += 1

                mapping_counts[
                    (
                        column,
                        int(lookup["class_number"]),
                        str(lookup["class_name"]),
                        code,
                        str(label),
                        status,
                    )
                ] += 1

            writer.writerow(row)

    # Replace only a temporary output in the new working folder. The raw input
    # file is never opened for writing and remains unchanged.
    temporary_output.replace(output_file)

    summary = {
        "hole_id": hole_id,
        "input_file": str(input_file),
        "tsg_lookup_file": str(tsg_file),
        "output_file": str(output_file),
        "rows": total_rows,
        "numeric_code_cells_before": numeric_codes_before,
        "resolved_code_cells": resolved_codes,
        "unresolved_code_cells": unresolved_codes,
        "numeric_code_cells_after": unresolved_codes,
        "all_numeric_codes_resolved": unresolved_codes == 0,
    }

    details = [
        {
            "hole_id": hole_id,
            "csv_column": column,
            "class_number": class_number,
            "class_name": class_name,
            "raw_code": code,
            "resolved_name": label,
            "occurrences": occurrences,
            "status": status,
        }
        for (
            column,
            class_number,
            class_name,
            code,
            label,
            status,
        ), occurrences in sorted(mapping_counts.items())
    ]
    return summary, details


def clean_raw_no_data_markers(
    hole_id: str,
    input_file: Path,
    output_file: Path,
    tracking_writer: csv.DictWriter,
) -> dict[str, object]:
    """Replace raw TSG no-data numbers with empty cells without deleting rows.

    This cleaning is applied only to the 20 sTSAS and uTSAS result columns.
    Identification and depth values are copied exactly as they appear in the
    code-resolved input file.

    sTSAS and uTSAS stay in separate columns. For example, if an sTSAS cell has
    no result but the matching uTSAS cell says ``Wood``, the sTSAS marker alone
    becomes empty and the uTSAS ``Wood`` label remains unchanged.
    """

    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_file.with_suffix(".csv.tmp")

    total_rows = 0
    rows_with_no_data_marker = 0
    stsas_markers_replaced = 0
    utsas_markers_replaced = 0

    # Use the CSV reader and writer so values that are not no-data markers are
    # copied as text without changing their displayed precision or spelling.
    with (
        input_file.open("r", encoding="utf-8", newline="") as source,
        temporary_output.open("w", encoding="utf-8", newline="") as destination,
    ):
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"{input_file}: CSV header is missing")
        writer = csv.DictWriter(destination, fieldnames=reader.fieldnames)
        writer.writeheader()

        for row in reader:
            total_rows += 1
            marker_found_in_row = False

            # Check all mineral, group, weight and error columns in both
            # interpretation families. A true measurement or readable label
            # is retained; only the special raw no-data number is replaced.
            for column in STSAS_COLUMNS + UTSAS_COLUMNS:
                raw_value = row[column].strip()
                try:
                    numeric_value = float(raw_value)
                except (TypeError, ValueError):
                    continue

                if math.isfinite(numeric_value) and numeric_value <= RAW_NO_DATA_LIMIT:
                    # Record the original cell BEFORE replacing it. CSV row 1
                    # is the header, so the first data record is CSV row 2.
                    # Depth alone is not an identifier because depths repeat.
                    # An empty output cell listed here came from a marker;
                    # an empty cell absent here was already empty in the input.
                    # This tracks representation, not the scientific reason
                    # why TSG produced a missing result.
                    tracking_writer.writerow({
                        "hole_id": hole_id,
                        "original_csv_row": total_rows + 1,
                        "column": column,
                        "original_value": row[column],
                        "change": "tsg_no_data_marker_to_empty",
                    })
                    row[column] = ""
                    marker_found_in_row = True
                    if column in STSAS_COLUMNS:
                        stsas_markers_replaced += 1
                    else:
                        utsas_markers_replaced += 1

            if marker_found_in_row:
                rows_with_no_data_marker += 1
            writer.writerow(row)

    # Replace only a temporary file in the new cleaned-data folder. Neither the
    # extracted input nor the code-resolved input is modified.
    try:
        temporary_output.replace(output_file)
    except PermissionError:
        # Excel may lock an existing CSV. Reuse it only if its complete bytes
        # match the freshly generated result. Otherwise stop, because the
        # tracking records must describe the actual cleaned output exactly.
        if output_file.is_file() and filecmp.cmp(
            temporary_output, output_file, shallow=False
        ):
            temporary_output.unlink()
        else:
            raise

    total_markers_replaced = stsas_markers_replaced + utsas_markers_replaced
    return {
        "hole_id": hole_id,
        "input_file": str(input_file),
        "output_file": str(output_file),
        "rows_before": total_rows,
        "rows_after": total_rows,
        "rows_removed": 0,
        "rows_with_no_data_marker": rows_with_no_data_marker,
        "stsas_no_data_markers_replaced": stsas_markers_replaced,
        "utsas_no_data_markers_replaced": utsas_markers_replaced,
        "total_no_data_markers_replaced": total_markers_replaced,
        "raw_no_data_markers_remaining": 0,
    }


def check_min_grp_wt_consistency(
    hole_id: str,
    cleaned_file: Path,
) -> list[dict[str, object]]:
    """Count Min/Grp/Wt presence patterns without changing the cleaned data.

    Each interpretation family has three positions. For example, position 1 is
    ``Min1``, ``Grp1`` and ``Wt1``. This check asks whether all three cells are
    populated, all three are empty, or only part of the set is populated.

    A mixed pattern is reported for review; it is not automatically called an
    error and its row is not removed. The report is evidence for deciding the
    next cleaning rule with the geological meaning kept in view.
    """

    # These eight patterns cover every possible present/empty combination for
    # one Min/Grp/Wt set. Clear names make the CSV readable without the code.
    pattern_names = {
        (True, True, True): "min_grp_wt_present",
        (False, False, False): "all_missing",
        (True, True, False): "min_grp_present_wt_missing",
        (True, False, True): "min_wt_present_grp_missing",
        (False, True, True): "grp_wt_present_min_missing",
        (True, False, False): "min_only",
        (False, True, False): "grp_only",
        (False, False, True): "wt_only",
    }

    # Keep one counter for every sTSAS/uTSAS position. Reading in chunks keeps
    # memory use stable for the largest drill-hole files.
    counters = {
        (family, position): Counter()
        for family in ("s_tsas", "u_tsas")
        for position in (1, 2, 3)
    }

    total_rows = 0
    for chunk in pd.read_csv(
        cleaned_file,
        chunksize=100_000,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    ):
        total_rows += len(chunk)

        for family in ("s_tsas", "u_tsas"):
            for position in (1, 2, 3):
                # Whitespace alone is treated as empty. Zero is still present,
                # which matters because a numeric weight of zero is a value.
                min_present = chunk[f"min{position}_{family}"].str.strip().ne("")
                grp_present = chunk[f"grp{position}_{family}"].str.strip().ne("")
                wt_present = chunk[f"wt{position}_{family}"].str.strip().ne("")

                counter = counters[(family, position)]
                for presence, pattern_name in pattern_names.items():
                    matches = (
                        min_present.eq(presence[0])
                        & grp_present.eq(presence[1])
                        & wt_present.eq(presence[2])
                    )
                    counter[pattern_name] += int(matches.sum())

    report_rows: list[dict[str, object]] = []
    for (family, position), counter in counters.items():
        mixed_rows = total_rows - (
            counter["min_grp_wt_present"] + counter["all_missing"]
        )
        report_rows.append({
            "hole_id": hole_id,
            "input_file": str(cleaned_file),
            "family": family,
            "position": position,
            "min_column": f"min{position}_{family}",
            "grp_column": f"grp{position}_{family}",
            "wt_column": f"wt{position}_{family}",
            "total_rows": total_rows,
            **{name: counter[name] for name in pattern_names.values()},
            "mixed_presence_rows": mixed_rows,
            "mixed_presence_percentage": round(100 * mixed_rows / total_rows, 2),
        })
    return report_rows


def compare_stsas_with_utsas(
    hole_id: str,
    cleaned_file: Path,
) -> dict[str, object]:
    """Compare the two SWIR interpretations without changing any values.

    A method is counted as having a result when its primary Min1/Grp1/Wt1 set
    is populated. Step 4 has already checked that these three cells are either
    all present or all empty, so Min1 can safely represent result presence.

    Agreement means the primary mineral name in Min1 is the same after removing
    surrounding spaces and ignoring capitalisation. Different weights are not
    used to decide mineral-name agreement; they are measurements to examine in
    a later numerical check.
    """

    counts: Counter[str] = Counter()

    # These are labels that describe tray or non-rock material rather than a
    # geological mineral result. Vegetation labels can have suffixes such as
    # ``Vegetation-Dry``, so they are matched by their starting word below.
    fixed_non_rock_labels = {"NOTAROK", "PLASTICCHIPTRAY", "WOOD"}

    for chunk in pd.read_csv(
        cleaned_file,
        chunksize=100_000,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    ):
        counts["total_rows"] += len(chunk)

        # Normalise only for comparison. The values written in the cleaned CSV
        # keep their original spelling and capitalisation.
        s_min1 = chunk["min1_s_tsas"].str.strip()
        u_min1 = chunk["min1_u_tsas"].str.strip()
        s_has_result = s_min1.ne("")
        u_has_result = u_min1.ne("")

        both_have = s_has_result & u_has_result
        both_missing = ~s_has_result & ~u_has_result
        s_only = s_has_result & ~u_has_result
        u_only = ~s_has_result & u_has_result

        counts["both_have_results"] += int(both_have.sum())
        counts["both_missing"] += int(both_missing.sum())
        counts["stsas_only"] += int(s_only.sum())
        counts["utsas_only"] += int(u_only.sum())

        same_primary_mineral = s_min1.str.upper().eq(u_min1.str.upper())
        counts["same_primary_mineral"] += int(
            (both_have & same_primary_mineral).sum()
        )
        counts["different_primary_mineral"] += int(
            (both_have & ~same_primary_mineral).sum()
        )

        # Search all Min and Grp fields because labels such as NOTAROK may be in
        # a group column while Wood or PlasticChipTray may be in a mineral one.
        family_non_rock: dict[str, pd.Series] = {}
        for family in ("s_tsas", "u_tsas"):
            non_rock = pd.Series(False, index=chunk.index)
            for prefix in ("min", "grp"):
                for position in (1, 2, 3):
                    values = chunk[f"{prefix}{position}_{family}"].str.strip().str.upper()
                    non_rock |= values.isin(fixed_non_rock_labels)
                    non_rock |= values.str.startswith("VEGETATION")
            family_non_rock[family] = non_rock

        counts["stsas_non_rock_rows"] += int(family_non_rock["s_tsas"].sum())
        counts["utsas_non_rock_rows"] += int(family_non_rock["u_tsas"].sum())
        counts["either_method_non_rock_rows"] += int(
            (family_non_rock["s_tsas"] | family_non_rock["u_tsas"]).sum()
        )

    # The first four counts must divide every input row into exactly one group.
    presence_total = sum(
        counts[name]
        for name in ("both_have_results", "both_missing", "stsas_only", "utsas_only")
    )
    if presence_total != counts["total_rows"]:
        raise ValueError(f"{cleaned_file}: comparison counts do not cover every row")

    return {
        "hole_id": hole_id,
        "input_file": str(cleaned_file),
        "total_rows": counts["total_rows"],
        "both_have_results": counts["both_have_results"],
        "same_primary_mineral": counts["same_primary_mineral"],
        "different_primary_mineral": counts["different_primary_mineral"],
        "stsas_only": counts["stsas_only"],
        "utsas_only": counts["utsas_only"],
        "both_missing": counts["both_missing"],
        "stsas_non_rock_rows": counts["stsas_non_rock_rows"],
        "utsas_non_rock_rows": counts["utsas_non_rock_rows"],
        "either_method_non_rock_rows": counts["either_method_non_rock_rows"],
    }


def export_primary_mineral_disagreements(
    hole_id: str,
    cleaned_file: Path,
    report_writer: csv.DictWriter,
) -> int:
    """Export rows where both methods have different Min1 mineral names.

    The function reads the cleaned data and writes review information only. It
    does not alter the cleaned CSV. The original CSV row number is included so
    a result remains traceable even when the same depth occurs more than once.
    """

    disagreement_count = 0
    rows_seen = 0
    fixed_non_rock_labels = {"NOTAROK", "PLASTICCHIPTRAY", "WOOD"}

    for chunk in pd.read_csv(
        cleaned_file,
        chunksize=100_000,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    ):
        # Compare normalised copies so spaces and capitalisation do not create
        # a false disagreement. Values exported to the report remain unchanged.
        s_min1 = chunk["min1_s_tsas"].str.strip()
        u_min1 = chunk["min1_u_tsas"].str.strip()
        both_have_results = s_min1.ne("") & u_min1.ne("")
        disagreement = both_have_results & s_min1.str.upper().ne(u_min1.str.upper())

        # Mark non-rock material using every Min and Grp position, because
        # NOTAROK may occur in a group column while tray/wood occurs in Min.
        family_non_rock: dict[str, pd.Series] = {}
        for family in ("s_tsas", "u_tsas"):
            non_rock = pd.Series(False, index=chunk.index)
            for prefix in ("min", "grp"):
                for position in (1, 2, 3):
                    values = chunk[f"{prefix}{position}_{family}"].str.strip().str.upper()
                    non_rock |= values.isin(fixed_non_rock_labels)
                    non_rock |= values.str.startswith("VEGETATION")
            family_non_rock[family] = non_rock

        # Only disagreement rows are written, keeping the review report small
        # enough for Excel while leaving all cleaned-data rows in place.
        for chunk_index in chunk.index[disagreement]:
            row = chunk.loc[chunk_index]
            s_non_rock = bool(family_non_rock["s_tsas"].loc[chunk_index])
            u_non_rock = bool(family_non_rock["u_tsas"].loc[chunk_index])
            report_writer.writerow({
                "hole_id": hole_id,
                # CSV row 1 is the header, so data row zero appears on row 2.
                "source_csv_row": rows_seen + int(chunk_index - chunk.index[0]) + 2,
                "start_depth": row["start_depth"],
                "end_depth": row["end_depth"],
                "min1_s_tsas": row["min1_s_tsas"],
                "grp1_s_tsas": row["grp1_s_tsas"],
                "wt1_s_tsas": row["wt1_s_tsas"],
                "error_s_tsas": row["error_s_tsas"],
                "min1_u_tsas": row["min1_u_tsas"],
                "grp1_u_tsas": row["grp1_u_tsas"],
                "wt1_u_tsas": row["wt1_u_tsas"],
                "error_u_tsas": row["error_u_tsas"],
                "stsas_non_rock": s_non_rock,
                "utsas_non_rock": u_non_rock,
                "either_method_non_rock": s_non_rock or u_non_rock,
                "comparison_category": (
                    "non_rock_involved"
                    if s_non_rock or u_non_rock
                    else "different_rock_minerals"
                ),
            })
            disagreement_count += 1

        rows_seen += len(chunk)

    return disagreement_count


def compare_complete_mineral_results(
    hole_id: str,
    cleaned_file: Path,
) -> dict[str, object]:
    """Compare all three mineral names from sTSAS with all three from uTSAS.

    The categories are mutually exclusive, so their counts add up to the total
    number of rows. Mineral names are normalised only while comparing: spaces
    and capitalisation are ignored, while the cleaned CSV remains unchanged.
    """

    counts: Counter[str] = Counter()
    fixed_non_rock_labels = {"NOTAROK", "PLASTICCHIPTRAY", "WOOD"}

    # Six mineral columns are enough to classify the mineral combinations. The
    # group columns are also loaded only to recognise a NOTAROK classification.
    columns = [
        *(f"min{position}_{family}"
          for family in ("s_tsas", "u_tsas")
          for position in (1, 2, 3)),
        *(f"grp{position}_{family}"
          for family in ("s_tsas", "u_tsas")
          for position in (1, 2, 3)),
    ]

    for chunk in pd.read_csv(
        cleaned_file,
        usecols=columns,
        chunksize=100_000,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    ):
        # ``usecols`` selects the requested fields but pandas may return them in
        # their original file order. Reorder explicitly before pairing each
        # tuple value with its column name.
        chunk = chunk[columns]
        for row in chunk.itertuples(index=False, name=None):
            values = dict(zip(columns, row))

            # Remove empty positions and normalise names for comparison only.
            s_minerals = tuple(
                str(values[f"min{position}_s_tsas"]).strip().upper()
                for position in (1, 2, 3)
                if str(values[f"min{position}_s_tsas"]).strip()
            )
            u_minerals = tuple(
                str(values[f"min{position}_u_tsas"]).strip().upper()
                for position in (1, 2, 3)
                if str(values[f"min{position}_u_tsas"]).strip()
            )

            if not s_minerals and not u_minerals:
                category = "both_missing"
            elif s_minerals and not u_minerals:
                category = "stsas_only"
            elif u_minerals and not s_minerals:
                category = "utsas_only"
            elif s_minerals == u_minerals:
                category = "same_minerals_same_order"
            elif sorted(s_minerals) == sorted(u_minerals):
                category = "same_minerals_different_order"
            elif set(u_minerals) < set(s_minerals):
                category = "stsas_has_additional_minerals"
            elif set(s_minerals) < set(u_minerals):
                category = "utsas_has_additional_minerals"
            else:
                category = "different_mineral_sets"

            counts[category] += 1
            counts["total_rows"] += 1

            # Count non-rock involvement separately. It does not replace the
            # mineral-comparison category assigned above.
            all_labels = [
                str(values[column]).strip().upper() for column in columns
            ]
            if any(
                label in fixed_non_rock_labels or label.startswith("VEGETATION")
                for label in all_labels
            ):
                counts["non_rock_involved"] += 1

    category_names = [
        "same_minerals_same_order",
        "same_minerals_different_order",
        "stsas_has_additional_minerals",
        "utsas_has_additional_minerals",
        "different_mineral_sets",
        "stsas_only",
        "utsas_only",
        "both_missing",
    ]
    classified_rows = sum(counts[name] for name in category_names)
    if classified_rows != counts["total_rows"]:
        raise ValueError(f"{cleaned_file}: full comparison missed some rows")

    return {
        "hole_id": hole_id,
        "input_file": str(cleaned_file),
        "total_rows": counts["total_rows"],
        **{name: counts[name] for name in category_names},
        "non_rock_involved": counts["non_rock_involved"],
    }


def compare_complete_group_results(
    hole_id: str,
    cleaned_file: Path,
) -> dict[str, object]:
    """Compare all three sTSAS groups with all three uTSAS groups.

    This check is separate from the mineral comparison. Two methods can choose
    different mineral names while still agreeing on a broader group, such as
    Hornblende and Actinolite both belonging to AMPHIBOLE.
    """

    counts: Counter[str] = Counter()
    columns = [
        f"grp{position}_{family}"
        for family in ("s_tsas", "u_tsas")
        for position in (1, 2, 3)
    ]

    for chunk in pd.read_csv(
        cleaned_file,
        usecols=columns,
        chunksize=100_000,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    ):
        # Keep a known order before pairing tuple values with column names.
        chunk = chunk[columns]
        for row in chunk.itertuples(index=False, name=None):
            values = dict(zip(columns, row))
            s_groups = tuple(
                str(values[f"grp{position}_s_tsas"]).strip().upper()
                for position in (1, 2, 3)
                if str(values[f"grp{position}_s_tsas"]).strip()
            )
            u_groups = tuple(
                str(values[f"grp{position}_u_tsas"]).strip().upper()
                for position in (1, 2, 3)
                if str(values[f"grp{position}_u_tsas"]).strip()
            )

            if not s_groups and not u_groups:
                category = "both_missing"
            elif s_groups and not u_groups:
                category = "stsas_only"
            elif u_groups and not s_groups:
                category = "utsas_only"
            elif s_groups == u_groups:
                category = "same_groups_same_order"
            elif sorted(s_groups) == sorted(u_groups):
                category = "same_groups_different_order"
            elif set(u_groups) < set(s_groups):
                category = "stsas_has_additional_groups"
            elif set(s_groups) < set(u_groups):
                category = "utsas_has_additional_groups"
            else:
                category = "different_group_sets"

            counts[category] += 1
            counts["total_rows"] += 1

    category_names = [
        "same_groups_same_order",
        "same_groups_different_order",
        "stsas_has_additional_groups",
        "utsas_has_additional_groups",
        "different_group_sets",
        "stsas_only",
        "utsas_only",
        "both_missing",
    ]
    if sum(counts[name] for name in category_names) != counts["total_rows"]:
        raise ValueError(f"{cleaned_file}: group comparison missed some rows")

    return {
        "hole_id": hole_id,
        "input_file": str(cleaned_file),
        "total_rows": counts["total_rows"],
        **{name: counts[name] for name in category_names},
    }


def validate_weights_and_error(
    hole_id: str,
    cleaned_file: Path,
) -> list[dict[str, object]]:
    """Check the numerical Wt and Error fields without changing the CSV.

    Empty cells are counted as missing. A weight total is considered close to
    one when it differs from 1 by no more than 0.001. This small tolerance
    allows normal floating-point rounding in the exported measurements.
    """

    tolerance = 0.001
    columns = [
        *(f"wt{position}_{family}"
          for family in ("s_tsas", "u_tsas")
          for position in (1, 2, 3)),
        "error_s_tsas",
        "error_u_tsas",
    ]
    data = pd.read_csv(
        cleaned_file,
        usecols=columns,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    )

    results: list[dict[str, object]] = []
    for family in ("s_tsas", "u_tsas"):
        weight_columns = [f"wt{position}_{family}" for position in (1, 2, 3)]
        error_column = f"error_{family}"

        # Empty is an allowed missing value. A populated cell that cannot be
        # converted to a finite number is reported as a numerical problem.
        weight_text = data[weight_columns].apply(lambda column: column.str.strip())
        weight_present = weight_text.ne("")
        numeric_weights = weight_text.apply(pd.to_numeric, errors="coerce")
        finite_weights = pd.DataFrame(
            np.isfinite(numeric_weights),
            index=numeric_weights.index,
            columns=numeric_weights.columns,
        )
        any_weight_present = weight_present.any(axis=1)
        all_weights_missing = ~any_weight_present
        nonnumeric_weight_cells = int(
            (weight_present & numeric_weights.isna()).sum().sum()
        )
        nonfinite_weight_cells = int(
            (weight_present & numeric_weights.notna() & ~finite_weights).sum().sum()
        )
        outside_range = int(
            (
                finite_weights
                & numeric_weights.notna()
                & (numeric_weights.lt(0) | numeric_weights.gt(1))
            ).sum().sum()
        )

        # Blank Wt2/Wt3 values are allowed when fewer minerals were identified.
        # Weight sums are checked only when every populated weight is numerical.
        invalid_weight_row = (
            weight_present & (numeric_weights.isna() | ~finite_weights)
        ).any(axis=1)
        checkable_sum = any_weight_present & ~invalid_weight_row
        weight_sums = numeric_weights.sum(axis=1, min_count=1)[checkable_sum]
        close_to_one = weight_sums.sub(1).abs().le(tolerance)

        error_text = data[error_column].str.strip()
        error_present = error_text.ne("")
        numeric_error = pd.to_numeric(error_text, errors="coerce")
        finite_error = pd.Series(np.isfinite(numeric_error), index=data.index)
        valid_error = error_present & numeric_error.notna() & finite_error
        valid_error_values = numeric_error[valid_error]

        results.append({
            "hole_id": hole_id,
            "input_file": str(cleaned_file),
            "family": family,
            "total_rows": len(data),
            "rows_with_weights": int(any_weight_present.sum()),
            "all_weights_missing_rows": int(all_weights_missing.sum()),
            "nonnumeric_weight_cells": nonnumeric_weight_cells,
            "nonfinite_weight_cells": nonfinite_weight_cells,
            "weight_cells_outside_0_to_1": outside_range,
            "weight_sum_rows_checked": len(weight_sums),
            "weight_sum_close_to_1_rows": int(close_to_one.sum()),
            "weight_sum_not_close_to_1_rows": int((~close_to_one).sum()),
            "weight_sum_min": weight_sums.min() if len(weight_sums) else "",
            "weight_sum_mean": weight_sums.mean() if len(weight_sums) else "",
            "weight_sum_median": weight_sums.median() if len(weight_sums) else "",
            "weight_sum_max": weight_sums.max() if len(weight_sums) else "",
            "error_missing_rows": int((~error_present).sum()),
            "error_nonnumeric_rows": int(
                (error_present & numeric_error.isna()).sum()
            ),
            "error_nonfinite_rows": int(
                (error_present & numeric_error.notna() & ~finite_error).sum()
            ),
            "error_negative_rows": int((valid_error & numeric_error.lt(0)).sum()),
            "error_min": valid_error_values.min() if len(valid_error_values) else "",
            "error_mean": valid_error_values.mean() if len(valid_error_values) else "",
            "error_median": valid_error_values.median() if len(valid_error_values) else "",
            "error_max": valid_error_values.max() if len(valid_error_values) else "",
            "weights_present_error_missing_rows": int(
                (any_weight_present & ~error_present).sum()
            ),
            "error_present_weights_missing_rows": int(
                (error_present & all_weights_missing).sum()
            ),
        })

    return results


def check_exact_duplicate_rows(
    hole_id: str,
    cleaned_file: Path,
) -> dict[str, object]:
    """Count completely identical rows without removing any of them.

    Every column is included in the comparison: hole ID, depths, and all sTSAS
    and uTSAS Min, Grp, Wt and Error values. ``exact_duplicate_rows`` includes
    the first copy in each duplicate group. ``removable_exact_duplicates``
    counts only the additional copies that remain after keeping one row.
    """

    # One drill-hole file fits comfortably in memory. Loading the complete file
    # lets pandas detect duplicates even when matching rows are far apart.
    data = pd.read_csv(
        cleaned_file,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    )
    total_rows = len(data)
    duplicate_mask = data.duplicated(keep=False)
    removable_mask = data.duplicated(keep="first")
    exact_duplicate_rows = int(duplicate_mask.sum())
    removable_exact_duplicates = int(removable_mask.sum())

    return {
        "hole_id": hole_id,
        "input_file": str(cleaned_file),
        "total_rows": total_rows,
        "unique_rows": total_rows - removable_exact_duplicates,
        "exact_duplicate_rows": exact_duplicate_rows,
        "removable_exact_duplicates": removable_exact_duplicates,
        "removable_percentage": round(
            100 * removable_exact_duplicates / total_rows, 2
        ) if total_rows else 0,
        "rows_removed": 0,
    }


def create_removable_duplicate_review(
    hole_id: str,
    cleaned_file: Path,
    review_file: Path,
) -> int:
    """Export additional exact copies for manual review without deleting them.

    The first occurrence of each identical record remains in the cleaned CSV
    and is not exported. Every later occurrence is written to the review file
    with its source row and the source row of the copy that would be retained.
    All 23 cleaned-data columns are included so the equality can be inspected.
    """

    data = pd.read_csv(
        cleaned_file,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    )

    # Assign the same internal group number to rows whose complete 23-column
    # records match. ``cumcount`` numbers appearances within each group.
    group_numbers = data.groupby(
        list(data.columns), sort=False, dropna=False
    ).ngroup()
    copy_number = data.groupby(group_numbers, sort=False).cumcount() + 1
    first_source_index = pd.Series(data.index, index=data.index).groupby(
        group_numbers, sort=False
    ).transform("min")
    removable = copy_number.gt(1)

    # Give only the duplicate groups simple sequential IDs for easier filtering
    # in Excel. CSV row numbers include the header, hence the addition of 2.
    review_group_ids, _ = pd.factorize(group_numbers[removable], sort=False)
    metadata = pd.DataFrame({
        "duplicate_group_id": review_group_ids + 1,
        "source_csv_row": data.index[removable] + 2,
        "kept_source_csv_row": first_source_index[removable].astype(int) + 2,
        "copy_number_in_group": copy_number[removable].astype(int),
    }, index=data.index[removable])
    review = pd.concat([metadata, data.loc[removable]], axis=1)

    # Verify that every exported row exactly matches the identified retained
    # row across all original data columns before creating the review artifact.
    for row_index in review.index:
        kept_index = int(first_source_index.loc[row_index])
        if not data.loc[row_index].equals(data.loc[kept_index]):
            raise ValueError(
                f"{cleaned_file}: duplicate row {row_index + 2} does not "
                f"match retained row {kept_index + 2}"
            )

    review_file.parent.mkdir(parents=True, exist_ok=True)
    review.to_csv(review_file, index=False)
    return len(review)


def check_duplicate_provenance(
    hole_id: str,
    resolved_file: Path,
    cleaned_file: Path,
) -> dict[str, object]:
    """Explain cleaned duplicates by comparing data before and after step 3.

    The code-resolved file still distinguishes a TSG no-data marker from an
    existing empty cell. The cleaned file represents both as empty. Comparing
    matching source rows tells us whether that conversion created a duplicate.
    This function reports evidence only and does not remove any rows.
    """

    read_options = {
        "dtype": "string",
        "keep_default_na": False,
        "na_filter": False,
    }
    resolved = pd.read_csv(resolved_file, **read_options)
    cleaned = pd.read_csv(cleaned_file, **read_options)
    if len(resolved) != len(cleaned) or list(resolved.columns) != list(cleaned.columns):
        raise ValueError(
            f"{hole_id}: resolved and cleaned files do not have matching rows/columns"
        )

    # In the cleaned file, assign equal complete rows to the same group. The
    # first row is retained; later rows in that group are removable copies.
    cleaned_groups = cleaned.groupby(
        list(cleaned.columns), sort=False, dropna=False
    ).ngroup()
    copy_number = cleaned.groupby(cleaned_groups, sort=False).cumcount() + 1
    first_index = pd.Series(cleaned.index, index=cleaned.index).groupby(
        cleaned_groups, sort=False
    ).transform("min")
    removable = copy_number.gt(1)

    # Compare every removable row with its retained partner in the earlier
    # code-resolved file, where marker values are still visible.
    removable_before = resolved.loc[removable].reset_index(drop=True)
    retained_before = resolved.iloc[
        first_index[removable].astype(int).to_numpy()
    ].reset_index(drop=True)
    already_exact_before = removable_before.eq(retained_before).all(axis=1)

    result_columns = STSAS_COLUMNS + UTSAS_COLUMNS
    cleaned_removable_results = cleaned.loc[removable, result_columns]
    stripped_results = cleaned_removable_results.apply(
        lambda column: column.astype(str).str.strip()
    )
    all_results_empty = stripped_results.eq("").all(axis=1)
    mineral_columns = [
        column for column in result_columns if column.startswith("min")
    ]
    stripped_minerals = cleaned.loc[removable, mineral_columns].apply(
        lambda column: column.astype(str).str.strip()
    )
    any_mineral_present = stripped_minerals.ne("").any(axis=1)

    # Count whether the removable row originally contained at least one TSG
    # marker. This remains traceable even though the cleaned cell is empty.
    original_numeric_results = resolved.loc[removable, result_columns].apply(
        pd.to_numeric, errors="coerce"
    )
    had_tsg_marker = original_numeric_results.le(RAW_NO_DATA_LIMIT).any(axis=1)

    return {
        "hole_id": hole_id,
        "resolved_file": str(resolved_file),
        "cleaned_file": str(cleaned_file),
        "total_rows": len(cleaned),
        "cleaned_exact_duplicate_rows": int(cleaned.duplicated(keep=False).sum()),
        "cleaned_removable_duplicates": int(removable.sum()),
        "already_exact_before_marker_conversion": int(already_exact_before.sum()),
        "became_duplicate_after_marker_conversion": int((~already_exact_before).sum()),
        "removable_rows_all_tsas_fields_empty": int(all_results_empty.sum()),
        "removable_rows_with_any_tsas_value": int((~all_results_empty).sum()),
        "removable_rows_with_any_mineral": int(any_mineral_present.sum()),
        "removable_rows_with_original_tsg_marker": int(had_tsg_marker.sum()),
        "removable_rows_without_original_tsg_marker": int((~had_tsg_marker).sum()),
        "rows_removed": 0,
    }


def check_repeated_depths(
    hole_id: str,
    cleaned_file: Path,
) -> dict[str, object]:
    """Describe repeated depths without assuming they are data errors.

    Multiple scanner observations can share a depth. For each repeated depth,
    this check distinguishes three cases: every complete row is identical; the
    mineral names agree but another value differs; or the mineral names differ.
    The cleaned file is read only and no repeated observation is removed.
    """

    data = pd.read_csv(
        cleaned_file,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
    )
    depth_columns = ["start_depth", "end_depth"]
    mineral_columns = [
        f"min{position}_{family}"
        for family in ("s_tsas", "u_tsas")
        for position in (1, 2, 3)
    ]

    # Count observations at every exact start/end depth pair. End depth has
    # already been validated against start depth, but keeping both makes the
    # grouping rule explicit and auditable.
    depth_counts = data.groupby(
        depth_columns, sort=False, dropna=False
    ).size().rename("rows_at_depth")

    # Count distinct complete records and distinct mineral combinations at each
    # depth. Dropping duplicates first provides exact comparisons without using
    # an approximate floating-point tolerance or hash collision assumption.
    complete_patterns = data.drop_duplicates().groupby(
        depth_columns, sort=False, dropna=False
    ).size().rename("complete_patterns")
    mineral_patterns = data[depth_columns + mineral_columns].drop_duplicates().groupby(
        depth_columns, sort=False, dropna=False
    ).size().rename("mineral_patterns")
    depth_summary = pd.concat(
        [depth_counts, complete_patterns, mineral_patterns], axis=1
    )
    repeated = depth_summary["rows_at_depth"].gt(1)

    # These three masks are mutually exclusive and cover every repeated depth.
    identical_records = repeated & depth_summary["complete_patterns"].eq(1)
    same_minerals_other_values_differ = (
        repeated
        & depth_summary["mineral_patterns"].eq(1)
        & depth_summary["complete_patterns"].gt(1)
    )
    different_minerals = repeated & depth_summary["mineral_patterns"].gt(1)
    repeated_depth_count = int(repeated.sum())
    classified_depth_count = int(
        identical_records.sum()
        + same_minerals_other_values_differ.sum()
        + different_minerals.sum()
    )
    if classified_depth_count != repeated_depth_count:
        raise ValueError(f"{cleaned_file}: repeated-depth classification is incomplete")

    return {
        "hole_id": hole_id,
        "input_file": str(cleaned_file),
        "total_rows": len(data),
        "unique_depth_values": len(depth_summary),
        "repeated_depth_values": repeated_depth_count,
        "rows_at_repeated_depths": int(
            depth_summary.loc[repeated, "rows_at_depth"].sum()
        ),
        "additional_rows_beyond_one_per_depth": int(
            (depth_summary.loc[repeated, "rows_at_depth"] - 1).sum()
        ),
        "maximum_rows_at_one_depth": int(depth_summary["rows_at_depth"].max()),
        "repeated_depths_with_identical_complete_rows": int(
            identical_records.sum()
        ),
        "rows_at_identical_repeated_depths": int(
            depth_summary.loc[identical_records, "rows_at_depth"].sum()
        ),
        "repeated_depths_same_minerals_other_values_differ": int(
            same_minerals_other_values_differ.sum()
        ),
        "rows_at_repeated_depths_same_minerals_other_values_differ": int(
            depth_summary.loc[
                same_minerals_other_values_differ, "rows_at_depth"
            ].sum()
        ),
        "repeated_depths_with_different_minerals": int(different_minerals.sum()),
        "rows_at_repeated_depths_with_different_minerals": int(
            depth_summary.loc[different_minerals, "rows_at_depth"].sum()
        ),
        "rows_removed": 0,
    }


def check_depth_gaps(
    hole_id: str,
    cleaned_file: Path,
) -> dict[str, object]:
    """Find unusually large jumps between consecutive recorded depths.

    The scanner spacing is not exactly the same for every drill hole. This
    check therefore calculates the typical spacing separately for each hole as
    the median positive change between its sorted unique start depths. A jump
    is reported as a gap when it is greater than twice that typical spacing.

    Repeated depths are reduced to one depth before calculating differences, so
    they cannot create false zero-length gaps. This is a reporting check only:
    it does not insert missing depths or remove any rows from the cleaned CSV.
    """

    data = pd.read_csv(cleaned_file, usecols=["start_depth"])

    # Convert the depth field to numbers and remove blanks or invalid values.
    # Sorting unique values gives the physical depth sequence for this hole.
    depths = np.sort(
        pd.to_numeric(data["start_depth"], errors="coerce").dropna().unique()
    )
    if len(depths) < 2:
        raise ValueError(f"{cleaned_file}: fewer than two usable depth values")

    # Positive differences describe the measured distance from one recorded
    # depth to the next. The median is robust to a small number of large gaps.
    differences = np.diff(depths)
    positive_differences = differences[differences > 0]
    typical_step = float(np.median(positive_differences))
    gap_threshold = typical_step * 2
    gap_mask = differences > gap_threshold
    gap_differences = differences[gap_mask]

    # Approximate how many normal measurement positions fit inside each gap.
    # This is an estimate because the scanner interval can vary slightly.
    estimated_missing_positions = int(
        np.maximum(np.rint(gap_differences / typical_step).astype(int) - 1, 1).sum()
    ) if gap_differences.size else 0

    # Record both sides of the largest jump so it can be found in the source
    # data. Empty values are used only if no gap was detected for this hole.
    if gap_differences.size:
        gap_indexes = np.flatnonzero(gap_mask)
        largest_index = int(gap_indexes[np.argmax(gap_differences)])
        largest_gap_from = float(depths[largest_index])
        largest_gap_to = float(depths[largest_index + 1])
        largest_gap_size = float(differences[largest_index])
    else:
        largest_gap_from = ""
        largest_gap_to = ""
        largest_gap_size = ""

    return {
        "hole_id": hole_id,
        "input_file": str(cleaned_file),
        "total_rows": len(data),
        "unique_depth_values": len(depths),
        "minimum_depth_m": round(float(depths[0]), 6),
        "maximum_depth_m": round(float(depths[-1]), 6),
        "typical_depth_step_m": round(typical_step, 6),
        "gap_rule": "depth jump > 2 x typical depth step",
        "gap_threshold_m": round(gap_threshold, 6),
        "depth_gap_count": int(gap_mask.sum()),
        "estimated_missing_depth_positions": estimated_missing_positions,
        "largest_gap_from_m": (
            round(largest_gap_from, 6) if largest_gap_from != "" else ""
        ),
        "largest_gap_to_m": (
            round(largest_gap_to, 6) if largest_gap_to != "" else ""
        ),
        "largest_gap_size_m": (
            round(largest_gap_size, 6) if largest_gap_size != "" else ""
        ),
        "rows_inserted": 0,
        "rows_removed": 0,
    }


def main() -> None:
    """Run validation, category-code resolution and no-data cleaning."""

    repository = Path(__file__).resolve().parents[1]
    data_root = repository.parent
    selection_file = repository / "configs" / "selections" / "team_raw_tsg_20.yaml"
    input_folder = data_root / "prepared" / "tsas_raw_extracted"
    resolved_folder = data_root / "prepared" / "tsas_code_resolved"
    cleaned_folder = data_root / "prepared" / "tsas_cleaned"
    extraction_report_file = input_folder / "tsas_raw_extraction_report.csv"
    output_folder = data_root / "prepared" / "tsas_cleaning_checks"
    output_folder.mkdir(parents=True, exist_ok=True)

    hole_ids = read_hole_ids(selection_file)
    extraction_report = pd.read_csv(extraction_report_file)
    expected_rows_by_hole = dict(
        zip(extraction_report["hole_id"], extraction_report["rows"])
    )

    report_rows = [
        validate_file(
            hole_id,
            input_folder / f"{hole_id}_stsas_utsas.csv",
            int(expected_rows_by_hole[hole_id])
            if hole_id in expected_rows_by_hole
            else None,
        )
        for hole_id in hole_ids
    ]

    report_file = output_folder / "01_input_validation.csv"
    write_report_csv(pd.DataFrame(report_rows), report_file)

    passed = sum(bool(row["validation_passed"]) for row in report_rows)
    print(f"Validated {len(report_rows)} drill-hole files: {passed} passed.")
    print(f"Report: {report_file}")

    # Stop before creating working files if the source structure did not pass.
    # This prevents code translation from hiding a file or drill-hole mismatch.
    if passed != len(report_rows):
        raise ValueError("Code resolution stopped because input validation failed")

    # The extraction report provides the exact raw package folder for each hole.
    package_folder_by_hole = dict(
        zip(extraction_report["hole_id"], extraction_report["package_folder"])
    )
    resolution_summaries: list[dict[str, object]] = []
    lookup_details: list[dict[str, object]] = []

    for hole_id in hole_ids:
        package_folder = Path(package_folder_by_hole[hole_id])
        tsg_file = find_swir_tsg_file(package_folder)
        summary, details = resolve_file_category_codes(
            hole_id=hole_id,
            input_file=input_folder / f"{hole_id}_stsas_utsas.csv",
            output_file=resolved_folder / f"{hole_id}_stsas_utsas.csv",
            tsg_file=tsg_file,
        )
        resolution_summaries.append(summary)
        lookup_details.extend(details)

    # The summary shows whether every numeric cell was resolved for each hole.
    resolution_report = output_folder / "02_numeric_code_resolution_summary.csv"
    write_report_csv(pd.DataFrame(resolution_summaries), resolution_report)

    # The detailed report shows the exact raw code, verified TSG name, column,
    # class table and occurrence count used for every replacement.
    lookup_report = output_folder / "02_numeric_code_lookup.csv"
    write_report_csv(pd.DataFrame(lookup_details), lookup_report)

    resolved_total = sum(
        int(row["resolved_code_cells"]) for row in resolution_summaries
    )
    unresolved_total = sum(
        int(row["unresolved_code_cells"]) for row in resolution_summaries
    )
    print(f"Resolved numeric category cells: {resolved_total}")
    print(f"Unresolved numeric category cells: {unresolved_total}")
    print(f"Resolution summary: {resolution_report}")
    print(f"Verified lookup details: {lookup_report}")

    # Do not continue to no-data cleaning unless every real numeric category
    # code has a verified name. This keeps an unresolved code visible as an
    # error rather than allowing it into the cleaned tables.
    if unresolved_total:
        raise ValueError("No-data cleaning stopped because category codes remain")

    # STEP 3: Replace only the raw TSG no-data number with an empty CSV cell.
    # This does not remove rows and does not combine sTSAS with uTSAS.
    # Keep cell-level provenance outside the analysis tables. Each tracking
    # record identifies exactly one changed cell in the code-resolved input.
    # Existing blanks are not changes and therefore are not listed here.
    # Stream records to disk: millions of changes need not be held in memory.
    tracking_file = output_folder / "03_no_data_marker_changes.csv"
    tracking_temporary = tracking_file.with_suffix(".csv.tmp")
    with tracking_temporary.open("w", encoding="utf-8", newline="") as tracking:
        tracking_writer = csv.DictWriter(tracking, fieldnames=[
            "hole_id", "original_csv_row", "column", "original_value", "change",
        ])
        tracking_writer.writeheader()
        no_data_summaries = [
            clean_raw_no_data_markers(
                hole_id=hole_id,
                input_file=resolved_folder / f"{hole_id}_stsas_utsas.csv",
                output_file=cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
                tracking_writer=tracking_writer,
            )
            for hole_id in hole_ids
        ]
    try:
        tracking_temporary.replace(tracking_file)
    except PermissionError:
        # The large tracking CSV may also be open. Keep the existing file only
        # when it exactly matches the freshly generated tracking records.
        if tracking_file.is_file() and filecmp.cmp(
            tracking_temporary, tracking_file, shallow=False
        ):
            tracking_temporary.unlink()
            print(f"Open tracking report remains valid: {tracking_file}")
        else:
            raise

    # This report makes the cleaning visible: it records the input and output
    # row counts and the number of sTSAS/uTSAS markers replaced for every hole.
    no_data_report = output_folder / "03_no_data_marker_cleaning.csv"
    write_report_csv(pd.DataFrame(no_data_summaries), no_data_report)

    markers_replaced = sum(
        int(row["total_no_data_markers_replaced"])
        for row in no_data_summaries
    )
    print(f"Raw no-data markers replaced with empty cells: {markers_replaced}")
    print(f"No-data cleaning report: {no_data_report}")
    print("The extracted source files were not changed and no rows were removed.")

    # STEP 4: Check Min/Grp/Wt sets in the cleaned files. This creates a report
    # only. It does not fill cells, delete rows or alter either TSAS family.
    consistency_rows: list[dict[str, object]] = []
    for hole_id in hole_ids:
        consistency_rows.extend(
            check_min_grp_wt_consistency(
                hole_id,
                cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
            )
        )

    consistency_report = output_folder / "04_min_grp_wt_consistency_check.csv"
    write_report_csv(pd.DataFrame(consistency_rows), consistency_report)
    mixed_total = sum(int(row["mixed_presence_rows"]) for row in consistency_rows)
    print(f"Min/Grp/Wt sets checked: {len(consistency_rows)}")
    print(f"Mixed-presence set occurrences for review: {mixed_total}")
    print(f"Min/Grp/Wt consistency report: {consistency_report}")

    # STEP 5: Compare the two interpretations at matching sample rows. This is
    # a report-only check; it does not choose one method or remove any rows.
    comparison_rows = [
        compare_stsas_with_utsas(
            hole_id,
            cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
        )
        for hole_id in hole_ids
    ]
    comparison_report = output_folder / "05_stsas_utsas_comparison.csv"
    write_report_csv(pd.DataFrame(comparison_rows), comparison_report)
    print(f"sTSAS/uTSAS drill holes compared: {len(comparison_rows)}")
    print(f"sTSAS/uTSAS comparison report: {comparison_report}")

    # STEP 6: Write the individual rows behind the disagreement count. This is
    # a review report only and does not modify the cleaned drill-hole CSVs.
    disagreement_report = output_folder / "06_primary_mineral_disagreements.csv"
    disagreement_temporary = disagreement_report.with_suffix(".csv.tmp")
    disagreement_fields = [
        "hole_id", "source_csv_row", "start_depth", "end_depth",
        "min1_s_tsas", "grp1_s_tsas", "wt1_s_tsas", "error_s_tsas",
        "min1_u_tsas", "grp1_u_tsas", "wt1_u_tsas", "error_u_tsas",
        "stsas_non_rock", "utsas_non_rock", "either_method_non_rock",
        "comparison_category",
    ]
    disagreement_counts: dict[str, int] = {}
    with disagreement_temporary.open(
        "w", encoding="utf-8", newline=""
    ) as disagreement_output:
        disagreement_writer = csv.DictWriter(
            disagreement_output, fieldnames=disagreement_fields
        )
        disagreement_writer.writeheader()
        for hole_id in hole_ids:
            disagreement_counts[hole_id] = export_primary_mineral_disagreements(
                hole_id,
                cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
                disagreement_writer,
            )

    # An open report is reused only when it exactly matches the new result.
    try:
        disagreement_temporary.replace(disagreement_report)
    except PermissionError:
        if disagreement_report.is_file() and filecmp.cmp(
            disagreement_temporary, disagreement_report, shallow=False
        ):
            disagreement_temporary.unlink()
        else:
            raise

    expected_disagreements = sum(
        int(row["different_primary_mineral"]) for row in comparison_rows
    )
    exported_disagreements = sum(disagreement_counts.values())
    if exported_disagreements != expected_disagreements:
        raise ValueError(
            "Detailed disagreement rows do not match the comparison summary"
        )
    print(f"Primary-mineral disagreements exported: {exported_disagreements}")
    print(f"Detailed disagreement report: {disagreement_report}")

    # STEP 7: Compare Min1, Min2 and Min3 as a complete mineral combination.
    # The result is one summary row per hole; the cleaned data is only read.
    complete_comparison_rows = [
        compare_complete_mineral_results(
            hole_id,
            cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
        )
        for hole_id in hole_ids
    ]
    complete_comparison_report = (
        output_folder / "07_complete_mineral_comparison.csv"
    )
    write_report_csv(
        pd.DataFrame(complete_comparison_rows), complete_comparison_report
    )
    print(f"Complete mineral combinations compared: {len(complete_comparison_rows)} holes")
    print(f"Complete mineral comparison report: {complete_comparison_report}")

    # STEP 8: Compare Grp1, Grp2 and Grp3 from the two methods. This creates a
    # summary report and makes no changes to the cleaned drill-hole files.
    complete_group_rows = [
        compare_complete_group_results(
            hole_id,
            cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
        )
        for hole_id in hole_ids
    ]
    complete_group_report = output_folder / "08_complete_group_comparison.csv"
    write_report_csv(pd.DataFrame(complete_group_rows), complete_group_report)
    print(f"Complete group combinations compared: {len(complete_group_rows)} holes")
    print(f"Complete group comparison report: {complete_group_report}")

    # STEP 9: Validate Wt1-Wt3 and Error for each interpretation family. This
    # produces a report only; numerical values in cleaned CSVs are unchanged.
    weight_error_rows: list[dict[str, object]] = []
    for hole_id in hole_ids:
        weight_error_rows.extend(
            validate_weights_and_error(
                hole_id,
                cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
            )
        )
    weight_error_report = output_folder / "09_weight_error_validation.csv"
    write_report_csv(pd.DataFrame(weight_error_rows), weight_error_report)
    print(f"Weight/error combinations checked: {len(weight_error_rows)}")
    print(f"Weight/error validation report: {weight_error_report}")

    # STEP 10: Count exact duplicates using every column. This is a check only;
    # no row is removed from the cleaned drill-hole files.
    duplicate_rows = [
        check_exact_duplicate_rows(
            hole_id,
            cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
        )
        for hole_id in hole_ids
    ]
    duplicate_report = output_folder / "10_exact_duplicate_check.csv"
    write_report_csv(pd.DataFrame(duplicate_rows), duplicate_report)
    print(f"Drill holes checked for exact duplicates: {len(duplicate_rows)}")
    print(f"Exact duplicate report: {duplicate_report}")

    # STEP 11: Compare each cleaned removable copy with the same source rows
    # before marker conversion. This tells us whether cleaning created apparent
    # duplicates and whether those copies contain any usable mineral results.
    duplicate_provenance_rows = [
        check_duplicate_provenance(
            hole_id,
            resolved_folder / f"{hole_id}_stsas_utsas.csv",
            cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
        )
        for hole_id in hole_ids
    ]
    duplicate_provenance_report = (
        output_folder / "11_duplicate_provenance_check.csv"
    )
    write_report_csv(
        pd.DataFrame(duplicate_provenance_rows), duplicate_provenance_report
    )
    print(f"Duplicate provenance checked: {len(duplicate_provenance_rows)} holes")
    print(f"Duplicate provenance report: {duplicate_provenance_report}")

    # STEP 12: Examine repeated depths. Multiple rows at one depth are reported
    # by result type and retained in the cleaned files.
    repeated_depth_rows = [
        check_repeated_depths(
            hole_id,
            cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
        )
        for hole_id in hole_ids
    ]
    repeated_depth_report = output_folder / "12_repeated_depth_check.csv"
    write_report_csv(pd.DataFrame(repeated_depth_rows), repeated_depth_report)
    print(f"Drill holes checked for repeated depths: {len(repeated_depth_rows)}")
    print(f"Repeated-depth report: {repeated_depth_report}")

    # STEP 13: Find large jumps in the ordered unique depths. Each drill hole
    # gets its own typical sampling interval and gap threshold. This reports
    # possible missing depth positions without filling or deleting any rows.
    depth_gap_rows = [
        check_depth_gaps(
            hole_id,
            cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
        )
        for hole_id in hole_ids
    ]
    depth_gap_report = output_folder / "13_depth_gap_check.csv"
    write_report_csv(pd.DataFrame(depth_gap_rows), depth_gap_report)
    print(f"Drill holes checked for depth gaps: {len(depth_gap_rows)}")
    print(f"Depth-gap report: {depth_gap_report}")


if __name__ == "__main__":
    main()

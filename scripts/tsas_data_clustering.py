"""Cluster the cleaned TSAS rows and compare what each interpretation concludes.

TSG reports two independent readings of the same SWIR measurement: sTSAS, the
system's automatic interpretation, and uTSAS, the user-tuned one. They name a
different primary mineral on about 6% of readings, and nearly all of those
disagreements are between members of the same mineral family, Ankerite against
Dolomite most of all.

Rather than pick one and hope the choice does not matter, this script runs the
whole pipeline twice, once per interpretation, through identical code with
identical settings, and then compares the two conclusions. Neither is treated
as correct; only an outside reference such as assay chemistry could decide
that. What the comparison can establish is whether the choice matters at all.

Steps 1 to 11 run once per interpretation. Step 12 compares them:

1. Read the cleaned paired sTSAS/uTSAS CSV for each selected drill hole,
   decide whether each row holds a usable rock interpretation, record
   why an unusable row is excluded, and combine the usable rows into one CSV
   with a per-hole selection summary.
2. Convert the selected Min1-Min3 and Wt1-Wt3 pairs into consistently named
   mineral-abundance columns while retaining the original sTSAS fields.
3. Summarise how often and how strongly every mineral occurs.
4. Average those rows into fixed depth intervals so that one row describes one
   length of core rather than one scanner reading.
5. Test a range of K-means cluster counts on those intervals and report the
   evidence behind the choice of K.
6. Choose K automatically with a fixed, documented rule, so a different set of
   drill holes runs end to end without a manual decision.
7. Fit that many clusters, name each one after its own mineral mixture, and
   save the cluster centres as a reusable model alongside the labelled
   intervals.
8. Compare the clusters against grouping by the single largest mineral, so the
   value added by the modelling is stated rather than assumed.
9. Summarise each drill hole by the share of its core in each cluster, then
   compare every pair of holes to find the closest mineral matches.
10. Rank every interval by how poorly it fits its own cluster, so unusual core
    can be reviewed instead of being absorbed into the nearest group.
11. Refit with different random starting seeds to show the grouping comes from
    the data rather than from where the search began.
12. Compare the two interpretations: whether the same metres group together,
    whether the hole-to-hole ranking changes, and whether the same core is
    flagged as unusual. The clusters are deliberately not matched one to one,
    because the two runs may choose different cluster counts and cluster
    numbers are arbitrary in any case.

Clustering uses the mineral-abundance columns directly. There is no PCA step:
the interval table is small, and clusters expressed in mineral units can be
read and challenged by a geologist, which matters more here than rotating the
features. PCA remains useful for plotting the result and can be added later
without changing how the clusters are formed.

K is chosen by rule rather than by hand, so the result is reproducible on a new
cohort. The rule prefers the simplest cluster count that is statistically as
good as the best one, and records what it gave up in doing so. Automating the
choice does not make it geologically right: the mineral profiles still need
review, and ``FIXED_CLUSTER_COUNT`` exists to overrule the rule when that review
disagrees with it.

This script does not edit or delete rows from the cleaned source CSVs. The
uTSAS columns are deliberately not used for selection because sTSAS is the
interpretation chosen for this analysis.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import NamedTuple
import re

import numpy as np
import pandas as pd


# These columns identify the original measurement. They are retained in the
# combined table, but they will not become numerical clustering features.
IDENTITY_COLUMNS = ["hole_id", "start_depth", "end_depth"]

# TSG produces two independent interpretations of the same SWIR measurement:
# sTSAS is the system's automatic result and uTSAS is the user-tuned one. They
# disagree on about 6% of readings, so an answer built on one of them is only
# trustworthy if the other leads to the same conclusion. Both are therefore
# clustered in the same run, with identical settings, and compared at the end.
# Neither is treated as the correct one; only an outside reference such as
# assay chemistry could decide that.
TSAS_FAMILIES = ("s_tsas", "u_tsas")


class FamilyColumns(NamedTuple):
    """The ten cleaned column names belonging to one interpretation."""

    family: str
    minerals: list[str]
    groups: list[str]
    weights: list[str]
    error: str
    all_fields: list[str]
    input_columns: list[str]


def family_columns(family: str) -> FamilyColumns:
    """Build the column names for one interpretation.

    The cleaned files hold both interpretations side by side with matching
    names, so every column this script needs follows from the family suffix.
    Deriving them here means the pipeline is written once and run twice,
    instead of being copied and edited for the second interpretation.
    """

    if family not in TSAS_FAMILIES:
        raise ValueError(f"Unknown TSAS family {family!r}; expected one of {TSAS_FAMILIES}")
    minerals = [f"min{position}_{family}" for position in (1, 2, 3)]
    groups = [f"grp{position}_{family}" for position in (1, 2, 3)]
    weights = [f"wt{position}_{family}" for position in (1, 2, 3)]
    error = f"error_{family}"
    all_fields = [*minerals, *groups, *weights, error]
    return FamilyColumns(
        family=family,
        minerals=minerals,
        groups=groups,
        weights=weights,
        error=error,
        all_fields=all_fields,
        input_columns=IDENTITY_COLUMNS + all_fields,
    )

# These values describe trays or other material rather than drill-core rock.
# Vegetation labels may include suffixes such as Vegetation-Dry, so they are
# recognised by their starting word in the selection function below.
FIXED_NON_ROCK_LABELS = {"NOTAROK", "PLASTICCHIPTRAY", "WOOD"}

# ``Aspectral`` means that the spectrum did not produce a diagnostic mineral
# interpretation. Its matching group is normally ``INVALID``. It is neither a
# rock mineral nor tray/wood, so it receives a separate exclusion reason.
NO_DIAGNOSTIC_MINERAL_LABELS = {"ASPECTRAL"}
INVALID_GROUP_LABELS = {"INVALID"}

# A valid set of mineral weights should total one. A small tolerance prevents
# harmless floating-point rounding from causing a row to be excluded.
WEIGHT_SUM_TOLERANCE = 0.001
CHUNK_SIZE = 100_000

# The scanner records roughly every 7 mm, so about 116 rows describe one metre
# of core. Those rows are not independent observations: consecutive rows measure
# the same rock, and a single 7 mm spot is too small to hold a real mineral
# mixture. Step 4 therefore averages the rows inside a fixed depth interval so
# one output row describes one length of core. One metre is the default because
# GSWA logs geology and assays chemistry at roughly that spacing, which keeps a
# later join to assay data straightforward. This is a reviewable choice rather
# than a property of the data, so it is kept as one named setting.
INTERVAL_METRES = 1.0

# An interval built from only a handful of scanner readings has not really been
# averaged, so it keeps the noisiness of a single reading while looking like a
# summary of a metre of core. Intervals below this many readings are therefore
# held out of clustering and listed in their own file. Set this to 0 to cluster
# every interval; at the default it excludes about 1.6% of them.
MINIMUM_INTERVAL_SOURCE_ROWS = 10

# Step 5 tests this range of cluster counts. Nothing in this script chooses the
# final K. The reports exist so the choice is made, and can be defended, by a
# person looking at both the statistics and the mineral meaning.
KMEANS_TEST_VALUES = range(2, 16)
KMEANS_RANDOM_STATE = 42
KMEANS_N_INIT = 10

# Step 6 chooses K automatically so a new set of drill holes does not need a
# person to read the comparison table before the pipeline can finish.
#
# The rule is deliberately not "take the highest silhouette". Splitting the data
# further nearly always raises that score a little, so a plain maximum drifts
# towards many small clusters that describe noise. Instead the rule takes the
# SIMPLEST solution that is statistically as good as the best one: among the
# cluster counts whose silhouette is within KMEANS_SILHOUETTE_TOLERANCE of the
# highest, the smallest K wins. A solution containing a cluster smaller than
# KMEANS_MINIMUM_CLUSTER_SHARE of the data is rejected first, because such a
# cluster usually describes a handful of odd intervals rather than a rock type.
KMEANS_SILHOUETTE_TOLERANCE = 0.02
KMEANS_MINIMUM_CLUSTER_SHARE = 0.01

# Set this to a whole number to overrule the automatic choice, for example when
# a geologist has reviewed the mineral profiles and prefers a different K. Leave
# it as None to let the rule above decide.
FIXED_CLUSTER_COUNT: int | None = None

# A cluster is named after its own average mineral mixture. When its leading
# mineral reaches this share of the mixture the cluster is called after that
# mineral alone; below it, the three leading minerals form a "Mixed" name.
CLUSTER_DOMINANT_MINERAL_SHARE = 0.5
CLUSTER_NAME_MINERAL_COUNT = 3

# An interval sitting far from its cluster's centre does not match the mineral
# mixture the cluster describes. Those are the intervals worth a second look.
# Distance is compared with the median distance inside the SAME cluster, because
# a loose cluster naturally has larger distances than a tight one, and a raw
# distance would simply rank every loose cluster's members as unusual.
# Intervals above this percentile of that ratio are flagged for review.
ODD_INTERVAL_FLAG_PERCENTILE = 99.0

# The final clustering is repeated with these random starting seeds to check the
# groups are a property of the data rather than of where the search happened to
# begin. Every other setting matches the final fit exactly, so the test measures
# the model that is actually used.
STABILITY_SEEDS = (42, 101, 202, 303, 404)


def read_selected_holes(selection_file: Path) -> list[str]:
    """Read the expected 20 drill-hole IDs from the project selection file."""

    # The selection file has a simple YAML list under ``holes:``. Reading that
    # small list directly keeps this script independent of an optional YAML
    # package that is not installed in the project's bundled Python runtime.
    hole_ids: list[str] = []
    inside_holes = False
    for raw_line in selection_file.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped == "holes:":
            inside_holes = True
            continue
        if inside_holes and stripped.startswith("- "):
            hole_ids.append(stripped[2:].strip().strip("'\""))
        elif inside_holes and stripped and not raw_line.startswith((" ", "\t")):
            break
    if len(hole_ids) != 20 or len(set(hole_ids)) != 20:
        raise ValueError(
            f"{selection_file}: expected 20 unique drill-hole IDs, "
            f"found {len(hole_ids)}"
        )
    return hole_ids


def non_rock_mask(data: pd.DataFrame, columns: FamilyColumns) -> pd.Series:
    """Return True for rows containing a non-rock label in this interpretation."""

    result = pd.Series(False, index=data.index)

    # Search both mineral and group fields. Wood and tray normally occur in a
    # mineral field, while NOTAROK can occur in a group field.
    for column in columns.minerals + columns.groups:
        labels = data[column].str.strip().str.upper()
        result |= labels.isin(FIXED_NON_ROCK_LABELS)
        result |= labels.str.startswith("VEGETATION")
    return result


def classify_rows(data: pd.DataFrame, columns: FamilyColumns) -> pd.DataFrame:
    """Add the clustering decision and reason to one cleaned-data chunk.

    A usable row must have a primary sTSAS mineral, internally consistent
    Min/Grp/Wt slots, valid weights that sum to one, and a valid non-negative
    Error value. Missing Min2 or Min3 is allowed when its matching group and
    weight are also missing.
    """

    # Empty CSV cells were loaded as empty strings so their origin remains
    # explicit. Strip spaces before testing whether a field is populated.
    text = data[columns.all_fields].apply(lambda column: column.str.strip())
    populated = text.ne("")

    all_stsas_missing = ~populated.any(axis=1)
    primary_mineral_missing = ~populated[columns.minerals[0]]
    is_non_rock = non_rock_mask(data, columns)
    no_diagnostic_mineral = (
        text[columns.minerals]
        .apply(lambda column: column.str.upper().isin(NO_DIAGNOSTIC_MINERAL_LABELS))
        .any(axis=1)
        | text[columns.groups]
        .apply(lambda column: column.str.upper().isin(INVALID_GROUP_LABELS))
        .any(axis=1)
    )

    # Each mineral position is consistent when Min, Grp and Wt are either all
    # present or all absent. For example, an absent Min2/Grp2/Wt2 is valid.
    inconsistent_slot = pd.Series(False, index=data.index)
    for position in (1, 2, 3):
        position_presence = populated[
            [
                columns.minerals[position - 1],
                columns.groups[position - 1],
                columns.weights[position - 1],
            ]
        ]
        # A slot is inconsistent when it contains a mixture of present and
        # absent fields. This vectorised comparison is fast on large files.
        inconsistent_slot |= (
            position_presence.any(axis=1) & ~position_presence.all(axis=1)
        )

    # Convert populated weights to numbers. A weight is invalid when it is not
    # numeric, is infinite, or lies outside the permitted zero-to-one range.
    numeric_weights = data[columns.weights].apply(pd.to_numeric, errors="coerce")
    supplied_weight = populated[columns.weights]
    finite_weights = pd.DataFrame(
        np.isfinite(numeric_weights),
        index=data.index,
        columns=columns.weights,
    )
    invalid_weight = (
        supplied_weight
        & (~finite_weights | numeric_weights.lt(0) | numeric_weights.gt(1))
    ).any(axis=1)

    # Empty optional weights contribute zero. Rows with a primary mineral must
    # have a total mineral weight very close to one.
    weight_sum = numeric_weights.fillna(0).sum(axis=1)
    invalid_weight_sum = (
        ~primary_mineral_missing
        & weight_sum.sub(1).abs().gt(WEIGHT_SUM_TOLERANCE)
    )

    # Error remains a quality field. We require a real, finite, non-negative
    # value but do not impose an unsupported maximum-error threshold.
    numeric_error = pd.to_numeric(data[columns.error], errors="coerce")
    valid_error = numeric_error.notna() & np.isfinite(numeric_error) & numeric_error.ge(0)
    invalid_error = ~valid_error

    # Apply mutually exclusive reasons in this order. The first applicable
    # reason explains why the row cannot enter the clustering input.
    reason = pd.Series("usable_stsas_rock", index=data.index, dtype="string")

    def set_reason(mask: pd.Series, label: str) -> None:
        reason.loc[reason.eq("usable_stsas_rock") & mask] = label

    set_reason(all_stsas_missing, "all_stsas_results_missing")
    set_reason(is_non_rock, "stsas_non_rock_material")
    set_reason(no_diagnostic_mineral, "stsas_no_diagnostic_mineral")
    set_reason(primary_mineral_missing, "primary_stsas_mineral_missing")
    set_reason(inconsistent_slot, "inconsistent_min_grp_wt_slot")
    set_reason(invalid_weight, "invalid_stsas_weight")
    set_reason(invalid_weight_sum, "stsas_weights_do_not_sum_to_one")
    set_reason(invalid_error, "invalid_or_missing_stsas_error")

    result = data.copy()
    result["use_for_clustering"] = reason.eq("usable_stsas_rock")
    result["selection_reason"] = reason
    return result


def process_hole(
    hole_id: str,
    columns: FamilyColumns,
    input_file: Path,
    selected_file: Path,
    decisions_file: Path,
    write_headers: bool,
) -> dict[str, object]:
    """Classify one hole and append its results to the combined output files."""

    if not input_file.is_file():
        raise FileNotFoundError(f"Missing cleaned file for {hole_id}: {input_file}")

    counts: Counter[str] = Counter()
    total_rows = 0

    # Chunked reading avoids holding every row from a large drill hole in RAM.
    for chunk in pd.read_csv(
        input_file,
        usecols=columns.input_columns,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
        chunksize=CHUNK_SIZE,
    ):
        # Confirm that the file contents correspond to its expected hole ID.
        if not chunk["hole_id"].eq(hole_id).all():
            raise ValueError(f"{input_file}: contains a different drill-hole ID")

        classified = classify_rows(chunk, columns)
        classified.insert(0, "source_csv_row", classified.index + 2)
        total_rows += len(classified)
        counts.update(classified["selection_reason"].value_counts().to_dict())

        # The selected table contains only rows that will continue to Step 2.
        selected = classified.loc[
            classified["use_for_clustering"],
            ["source_csv_row"] + columns.input_columns + ["selection_reason"],
        ]
        selected.to_csv(
            selected_file,
            mode="w" if write_headers else "a",
            header=write_headers,
            index=False,
        )

        # This compact audit table records the decision for every source row.
        decisions = classified[
            ["source_csv_row"]
            + IDENTITY_COLUMNS
            + ["use_for_clustering", "selection_reason"]
        ]
        decisions.to_csv(
            decisions_file,
            mode="w" if write_headers else "a",
            header=write_headers,
            index=False,
        )
        write_headers = False

    selected_rows = counts["usable_stsas_rock"]
    return {
        "hole_id": hole_id,
        "input_file": str(input_file),
        "total_rows": total_rows,
        "selected_rows": selected_rows,
        "excluded_rows": total_rows - selected_rows,
        "selected_percentage": round(100 * selected_rows / total_rows, 2),
        "all_stsas_results_missing": counts["all_stsas_results_missing"],
        "stsas_non_rock_material": counts["stsas_non_rock_material"],
        "stsas_no_diagnostic_mineral": counts[
            "stsas_no_diagnostic_mineral"
        ],
        "primary_stsas_mineral_missing": counts["primary_stsas_mineral_missing"],
        "inconsistent_min_grp_wt_slot": counts["inconsistent_min_grp_wt_slot"],
        "invalid_stsas_weight": counts["invalid_stsas_weight"],
        "stsas_weights_do_not_sum_to_one": counts[
            "stsas_weights_do_not_sum_to_one"
        ],
        "invalid_or_missing_stsas_error": counts[
            "invalid_or_missing_stsas_error"
        ],
        "source_rows_changed": 0,
    }


def abundance_column_name(mineral: str) -> str:
    """Create a stable CSV column name from an sTSAS mineral label."""

    safe_name = re.sub(r"[^a-z0-9]+", "_", mineral.lower()).strip("_")
    return f"abundance_{safe_name}"


def collect_mineral_names(selected_file: Path, columns: FamilyColumns) -> list[str]:
    """Find every distinct mineral used by the selected sTSAS rows."""

    mineral_names: set[str] = set()
    for chunk in pd.read_csv(
        selected_file,
        usecols=columns.minerals,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
        chunksize=CHUNK_SIZE,
    ):
        for column in columns.minerals:
            labels = chunk[column].str.strip()
            mineral_names.update(labels.loc[labels.ne("")].tolist())

    if not mineral_names:
        raise ValueError(f"{selected_file}: no mineral names were found")

    ordered_names = sorted(mineral_names)
    output_names = [abundance_column_name(name) for name in ordered_names]
    if len(output_names) != len(set(output_names)):
        raise ValueError("Two mineral labels produce the same abundance column name")
    return ordered_names


def create_mineral_abundance_table(
    selected_file: Path,
    columns: FamilyColumns,
    output_file: Path,
    expected_rows: int,
) -> dict[str, object]:
    """Create one numerical abundance column for every selected mineral.

    Every selected input row remains exactly one output row. The original
    Min/Grp/Wt/Error fields remain beside the derived abundance fields so a
    reader can trace whether a mineral originally appeared as Min1, Min2 or
    Min3. Only the abundance fields are intended as modelling features.
    """

    mineral_names = collect_mineral_names(selected_file, columns)
    abundance_columns = [abundance_column_name(name) for name in mineral_names]
    mineral_index = {name: index for index, name in enumerate(mineral_names)}

    # Write to a temporary file first. The final filename appears only after
    # every row and validation check succeeds, avoiding a partial final CSV.
    temporary_file = output_file.with_suffix(".tmp.csv")
    temporary_file.unlink(missing_ok=True)
    output_file.unlink(missing_ok=True)

    input_columns = ["source_csv_row"] + columns.input_columns
    numeric_original_columns = [
        "source_csv_row",
        "start_depth",
        "end_depth",
        *columns.weights,
        columns.error,
    ]
    total_rows = 0
    invalid_abundance_sums = 0
    source_to_abundance_mismatches = 0
    maximum_nonzero_minerals = 0
    write_header = True

    for chunk in pd.read_csv(
        selected_file,
        usecols=input_columns,
        dtype="string",
        keep_default_na=False,
        na_filter=False,
        chunksize=CHUNK_SIZE,
    ):
        # Use a dense numerical block for the CSV. Each row has only one to
        # three non-zero values even though all 44 mineral columns are present.
        abundance_values = np.zeros(
            (len(chunk), len(mineral_names)), dtype=np.float64
        )

        for position in (1, 2, 3):
            mineral_column = columns.minerals[position - 1]
            weight_column = columns.weights[position - 1]
            labels = chunk[mineral_column].str.strip()
            weights = pd.to_numeric(chunk[weight_column], errors="coerce")
            mapped_columns = labels.map(mineral_index)

            # Every populated mineral must have been included in the mineral
            # list and must have a numerical matching weight.
            populated = labels.ne("")
            invalid_mapping = populated & (mapped_columns.isna() | weights.isna())
            if invalid_mapping.any():
                raise ValueError(
                    f"{selected_file}: a populated mineral could not be converted"
                )

            valid = populated & mapped_columns.notna() & weights.notna()
            row_indexes = np.flatnonzero(valid.to_numpy())
            column_indexes = mapped_columns.loc[valid].astype(int).to_numpy()
            numeric_values = weights.loc[valid].to_numpy(dtype=float)

            # np.add.at also remains correct if the same mineral ever appears
            # in two positions on one row: its two weights would be combined.
            np.add.at(
                abundance_values,
                (row_indexes, column_indexes),
                numeric_values,
            )

        abundance = pd.DataFrame(
            abundance_values,
            index=chunk.index,
            columns=abundance_columns,
        )
        abundance_sum = abundance.sum(axis=1)
        original_weight_sum = (
            chunk[columns.weights]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0)
            .sum(axis=1)
        )

        # These checks prove that the conversion preserved the original weights
        # and that each selected mineral mixture totals approximately one.
        invalid_abundance_sums += int(
            abundance_sum.sub(1).abs().gt(WEIGHT_SUM_TOLERANCE).sum()
        )
        source_to_abundance_mismatches += int(
            abundance_sum.sub(original_weight_sum).abs().gt(1e-9).sum()
        )
        maximum_nonzero_minerals = max(
            maximum_nonzero_minerals,
            int(abundance.ne(0).sum(axis=1).max()),
        )

        # Store original numbers as real numeric values in the output CSV.
        original = chunk[input_columns].copy().reset_index(drop=True)
        for column in numeric_original_columns:
            original[column] = pd.to_numeric(original[column], errors="coerce")

        output_chunk = pd.concat(
            [original, abundance.reset_index(drop=True)], axis=1
        )
        output_chunk["abundance_sum_check"] = abundance_sum.round(6).to_numpy()
        output_chunk.to_csv(
            temporary_file,
            mode="w" if write_header else "a",
            header=write_header,
            index=False,
        )
        write_header = False
        total_rows += len(output_chunk)

    if total_rows != expected_rows:
        raise ValueError(
            f"Abundance output has {total_rows:,} rows; expected {expected_rows:,}"
        )
    if invalid_abundance_sums:
        raise ValueError(
            f"{invalid_abundance_sums:,} abundance rows do not total approximately one"
        )
    if source_to_abundance_mismatches:
        raise ValueError(
            f"{source_to_abundance_mismatches:,} abundance rows changed source weights"
        )
    if maximum_nonzero_minerals > 3:
        raise ValueError("An abundance row contains more than three minerals")

    temporary_file.replace(output_file)
    return {
        "rows": total_rows,
        "mineral_abundance_columns": len(mineral_names),
        "invalid_abundance_sums": invalid_abundance_sums,
        "source_to_abundance_mismatches": source_to_abundance_mismatches,
        "maximum_nonzero_minerals_in_one_row": maximum_nonzero_minerals,
    }


def create_mineral_abundance_summary(
    abundance_file: Path,
    columns: FamilyColumns,
    summary_file: Path,
    expected_rows: int,
) -> pd.DataFrame:
    """Summarise every mineral column without changing the abundance data.

    This is a review step before modelling. For each mineral, the report shows:

    * how many selected rows contain it;
    * what percentage of all selected rows contain it;
    * how many of the 20 drill holes contain it;
    * how often it originally appeared as Min1, Min2, or Min3; and
    * its total, average, and maximum abundance.

    A mineral is counted as present in a row when its abundance is greater
    than zero. The source abundance CSV is only read; no rows, columns, or
    mineral values are removed or edited by this function.
    """

    if not abundance_file.is_file():
        raise FileNotFoundError(f"Missing mineral-abundance file: {abundance_file}")

    # Read only the header first so this function automatically uses every
    # abundance column created by Step 2. ``abundance_sum_check`` is a check,
    # not a mineral feature, so its name does not match this ending rule.
    all_columns = pd.read_csv(abundance_file, nrows=0).columns.tolist()
    abundance_columns = [
        column
        for column in all_columns
        if column.startswith("abundance_") and column != "abundance_sum_check"
    ]
    if not abundance_columns:
        raise ValueError(f"{abundance_file}: no mineral-abundance columns found")

    # Each Step 2 mineral name maps to one stable abundance column. Recovering
    # names from Min1-Min3 keeps the exact readable labels, including capitals
    # and hyphens, in the report.
    mineral_name_by_column: dict[str, str] = {}
    rows_present = Counter()
    holes_present: dict[str, set[str]] = {
        column: set() for column in abundance_columns
    }
    position_counts: dict[int, Counter[str]] = {
        position: Counter() for position in (1, 2, 3)
    }
    total_abundance = Counter()
    maximum_abundance = Counter()
    total_rows = 0

    columns_to_read = ["hole_id", *columns.minerals, *abundance_columns]
    for chunk in pd.read_csv(
        abundance_file,
        usecols=columns_to_read,
        chunksize=CHUNK_SIZE,
    ):
        total_rows += len(chunk)

        # Count the original ranked positions. These counts let us see whether
        # a mineral usually occurs as the primary (Min1), second (Min2), or
        # third (Min3) mineral even though clustering uses combined abundance.
        for position, mineral_column in enumerate(columns.minerals, start=1):
            labels = chunk[mineral_column].dropna().astype(str).str.strip()
            labels = labels.loc[labels.ne("")]
            position_counts[position].update(labels.value_counts().to_dict())
            for mineral_name in labels.unique():
                output_column = abundance_column_name(mineral_name)
                previous_name = mineral_name_by_column.get(output_column)
                if previous_name is not None and previous_name != mineral_name:
                    raise ValueError(
                        "Two mineral labels map to the same abundance column: "
                        f"{previous_name!r} and {mineral_name!r}"
                    )
                mineral_name_by_column[output_column] = mineral_name

        numeric_abundance = chunk[abundance_columns].fillna(0).astype(float)
        for column in abundance_columns:
            values = numeric_abundance[column]
            present = values.gt(0)
            rows_present[column] += int(present.sum())
            total_abundance[column] += float(values.sum())
            maximum_abundance[column] = max(
                maximum_abundance[column], float(values.max())
            )
            if present.any():
                holes_present[column].update(
                    chunk.loc[present, "hole_id"].dropna().astype(str).unique()
                )

    if total_rows != expected_rows:
        raise ValueError(
            f"Summary read {total_rows:,} rows; expected {expected_rows:,}"
        )
    if set(mineral_name_by_column) != set(abundance_columns):
        missing_names = sorted(set(abundance_columns) - set(mineral_name_by_column))
        raise ValueError(
            "Could not recover mineral names for abundance columns: "
            + ", ".join(missing_names)
        )

    summary_rows: list[dict[str, object]] = []
    for column in abundance_columns:
        mineral_name = mineral_name_by_column[column]
        present_count = rows_present[column]
        abundance_total = total_abundance[column]
        summary_rows.append(
            {
                "mineral_name": mineral_name,
                "abundance_column": column,
                "rows_present": present_count,
                "percentage_of_selected_rows": round(
                    100 * present_count / total_rows, 4
                ),
                "holes_present": len(holes_present[column]),
                "min1_count": position_counts[1][mineral_name],
                "min2_count": position_counts[2][mineral_name],
                "min3_count": position_counts[3][mineral_name],
                "total_abundance": round(abundance_total, 6),
                "mean_abundance_when_present": round(
                    abundance_total / present_count, 6
                ),
                "mean_abundance_across_all_rows": round(
                    abundance_total / total_rows, 6
                ),
                "maximum_abundance": round(maximum_abundance[column], 6),
            }
        )

    # Put the most commonly occurring minerals first so the report is easy to
    # inspect. The alphabetical mineral name resolves any equal row counts.
    summary = pd.DataFrame(summary_rows).sort_values(
        ["rows_present", "mineral_name"],
        ascending=[False, True],
        ignore_index=True,
    )
    summary.insert(0, "prevalence_rank", range(1, len(summary) + 1))

    # Validate that all original Min1-Min3 occurrences are represented in the
    # report. This proves the summary includes all three ranked mineral fields.
    source_position_total = sum(
        int(summary[column].sum())
        for column in ("min1_count", "min2_count", "min3_count")
    )
    expected_position_total = sum(
        sum(counter.values()) for counter in position_counts.values()
    )
    if source_position_total != expected_position_total:
        raise ValueError("Mineral position counts were not preserved in summary")

    summary_file.unlink(missing_ok=True)
    summary.to_csv(summary_file, index=False)
    return summary


def create_interval_abundance_table(
    abundance_file: Path,
    interval_file: Path,
    expected_holes: list[str],
    expected_rows: int,
    interval_metres: float = INTERVAL_METRES,
) -> dict[str, object]:
    """Average the mineral abundances inside each fixed depth interval.

    One output row describes one interval of core in one drill hole, instead of
    one scanner reading. Every input row contributes to exactly one interval,
    and an interval's mineral values are the plain mean of its rows.

    Because each input row's abundances already total one, the mean of those
    rows also totals one. An interval is therefore still a complete mineral
    mixture and stays directly comparable with any other interval.

    Intervals containing no measured rows are not invented. A gap in the core
    simply produces no output row, so missing core stays missing.

    The abundance file is read only. No row is deleted and no mineral value is
    edited; rows are combined, and the number combined is recorded for review.
    """

    if interval_metres <= 0:
        raise ValueError("The depth interval must be greater than zero metres")
    if not abundance_file.is_file():
        raise FileNotFoundError(f"Missing mineral-abundance file: {abundance_file}")

    abundance_columns = [
        column
        for column in pd.read_csv(abundance_file, nrows=0).columns
        if column.startswith("abundance_") and column != "abundance_sum_check"
    ]
    if not abundance_columns:
        raise ValueError(f"{abundance_file}: no mineral-abundance columns found")

    # Each chunk is reduced to per-interval totals immediately. An interval that
    # spans a chunk boundary is completed by summing its partial totals below,
    # so the result does not depend on where the chunks happen to split.
    partial_sums: list[pd.DataFrame] = []
    partial_details: list[pd.DataFrame] = []
    total_rows = 0

    for chunk in pd.read_csv(
        abundance_file,
        usecols=["hole_id", "start_depth", "end_depth", *abundance_columns],
        chunksize=CHUNK_SIZE,
    ):
        total_rows += len(chunk)

        # The midpoint is used so the interval remains correct if a future
        # record describes a real depth range rather than a point measurement.
        depth = (
            pd.to_numeric(chunk["start_depth"], errors="raise")
            + pd.to_numeric(chunk["end_depth"], errors="raise")
        ) / 2
        if not np.isfinite(depth).all():
            raise ValueError(f"{abundance_file}: a row has a missing depth")

        chunk = chunk.assign(
            interval_index=np.floor(depth / interval_metres).astype(np.int64),
            measurement_depth=depth,
        )
        grouped = chunk.groupby(["hole_id", "interval_index"], sort=False)
        partial_sums.append(grouped[abundance_columns].sum())
        partial_details.append(
            pd.DataFrame(
                {
                    "source_rows": grouped.size(),
                    "shallowest_measured_depth": grouped["measurement_depth"].min(),
                    "deepest_measured_depth": grouped["measurement_depth"].max(),
                }
            )
        )

    if total_rows != expected_rows:
        raise ValueError(
            f"Interval binning read {total_rows:,} rows; expected {expected_rows:,}"
        )

    # Complete any interval that was split across two chunks.
    sums = pd.concat(partial_sums).groupby(level=[0, 1], sort=False).sum()
    details = pd.concat(partial_details)
    grouped_details = details.groupby(level=[0, 1], sort=False)
    details = pd.DataFrame(
        {
            "source_rows": grouped_details["source_rows"].sum(),
            "shallowest_measured_depth": grouped_details[
                "shallowest_measured_depth"
            ].min(),
            "deepest_measured_depth": grouped_details[
                "deepest_measured_depth"
            ].max(),
        }
    )
    details = details.reindex(sums.index)
    if int(details["source_rows"].sum()) != expected_rows:
        raise ValueError("Interval binning lost or duplicated source rows")

    # Dividing each interval's totals by its row count gives the mean mixture.
    means = sums.div(details["source_rows"], axis=0)

    interval_index = means.index.get_level_values("interval_index")
    output = pd.DataFrame(
        {
            "hole_id": means.index.get_level_values("hole_id").astype(str),
            "interval_start": interval_index * interval_metres,
            "interval_end": (interval_index + 1) * interval_metres,
            "interval_metres": interval_metres,
            "source_rows": details["source_rows"].to_numpy(),
            "shallowest_measured_depth": details[
                "shallowest_measured_depth"
            ].to_numpy(),
            "deepest_measured_depth": details["deepest_measured_depth"].to_numpy(),
            "minerals_present": means.gt(0).sum(axis=1).to_numpy(),
        }
    ).reset_index(drop=True)
    output = pd.concat([output, means.reset_index(drop=True)], axis=1)
    output["abundance_sum_check"] = means.sum(axis=1).to_numpy()

    # Keep the drill-hole order of the selection file, then order by depth, so
    # the output reads like the core rather than like the grouping operation.
    hole_order = {hole_id: position for position, hole_id in enumerate(expected_holes)}
    unexpected_holes = sorted(set(output["hole_id"]) - set(hole_order))
    if unexpected_holes:
        raise ValueError(
            "Interval binning found unexpected drill holes: "
            + ", ".join(unexpected_holes)
        )
    output = (
        output.assign(hole_order=output["hole_id"].map(hole_order))
        .sort_values(["hole_order", "interval_start"], ignore_index=True)
        .drop(columns="hole_order")
    )

    # Every interval must still be a complete mineral mixture totalling one.
    invalid_sums = int(
        output["abundance_sum_check"].sub(1).abs().gt(WEIGHT_SUM_TOLERANCE).sum()
    )
    if invalid_sums:
        raise ValueError(
            f"{invalid_sums:,} interval rows do not total approximately one"
        )

    interval_file.unlink(missing_ok=True)
    output.to_csv(interval_file, index=False)

    return {
        "interval_metres": interval_metres,
        "source_rows": total_rows,
        "interval_rows": len(output),
        "holes": int(output["hole_id"].nunique()),
        "mineral_columns": len(abundance_columns),
        "mean_source_rows_per_interval": round(
            float(output["source_rows"].mean()), 1
        ),
        "smallest_source_rows_in_an_interval": int(output["source_rows"].min()),
        "mean_minerals_per_interval": round(
            float(output["minerals_present"].mean()), 1
        ),
        "fewest_minerals_in_an_interval": int(output["minerals_present"].min()),
        "most_minerals_in_an_interval": int(output["minerals_present"].max()),
    }


def compare_cluster_counts(
    interval_file: Path,
    comparison_file: Path,
    cluster_sizes_file: Path,
    excluded_file: Path,
    cluster_counts: range = KMEANS_TEST_VALUES,
    minimum_source_rows: int = MINIMUM_INTERVAL_SOURCE_ROWS,
    random_state: int = KMEANS_RANDOM_STATE,
) -> pd.DataFrame:
    """Test a range of cluster counts on the interval mineral abundances.

    Clustering uses the mineral-abundance columns only. The hole ID, depths and
    row counts describe where an interval is, not what it is made of, so they
    are deliberately excluded from the model.

    The interval table is small enough that this uses ordinary K-means rather
    than a mini-batch approximation, and that every separation score is computed
    on every clustered row instead of on a sample. The scores are therefore
    exact for this data rather than an estimate.

    This function does not choose K. It writes the evidence for that decision:
    a summary row for each K, and the individual size of every cluster so that
    a solution propped up by one dominant group can be recognised.

    No source file is edited. Intervals held out for having too few readings
    are written to their own file rather than deleted.
    """

    # Import the optional modelling package only when this step runs, so the
    # earlier preparation steps still work without scikit-learn installed.
    from sklearn.cluster import KMeans
    from sklearn.metrics import (
        calinski_harabasz_score,
        davies_bouldin_score,
        silhouette_score,
    )

    if not interval_file.is_file():
        raise FileNotFoundError(f"Missing interval abundance file: {interval_file}")

    intervals = pd.read_csv(interval_file)
    abundance_columns = [
        column
        for column in intervals.columns
        if column.startswith("abundance_") and column != "abundance_sum_check"
    ]
    if not abundance_columns:
        raise ValueError(f"{interval_file}: no mineral-abundance columns found")

    # Hold back thinly measured intervals, and record exactly which ones, so the
    # decision stays visible instead of silently shaping the result.
    thin = intervals["source_rows"].lt(minimum_source_rows)
    excluded_file.unlink(missing_ok=True)
    intervals.loc[
        thin,
        [
            "hole_id",
            "interval_start",
            "interval_end",
            "source_rows",
            "minerals_present",
        ],
    ].assign(
        reason=f"fewer_than_{minimum_source_rows}_scanner_rows"
    ).to_csv(excluded_file, index=False)

    clustered = intervals.loc[~thin].reset_index(drop=True)
    features = clustered[abundance_columns].to_numpy(dtype=np.float64)
    if not np.isfinite(features).all():
        raise ValueError(f"{interval_file}: an abundance value is missing or infinite")
    if len(features) < 2:
        raise ValueError("Too few intervals remain to cluster")

    largest_tested = max(cluster_counts)
    if largest_tested >= len(features):
        raise ValueError(
            f"Cannot test {largest_tested} clusters with {len(features):,} intervals"
        )

    comparison_rows: list[dict[str, object]] = []
    cluster_size_rows: list[dict[str, object]] = []

    for cluster_count in cluster_counts:
        model = KMeans(
            n_clusters=cluster_count,
            init="k-means++",
            n_init=KMEANS_N_INIT,
            random_state=random_state,
        )
        labels = model.fit_predict(features)
        sizes = np.bincount(labels, minlength=cluster_count)

        # Every score below uses all clustered rows, so they are directly
        # comparable between cluster counts without a sampling caveat.
        comparison_rows.append(
            {
                "k_clusters": cluster_count,
                "clustered_intervals": len(features),
                "mineral_features_used": len(abundance_columns),
                "inertia": float(model.inertia_),
                "inertia_per_interval": float(model.inertia_ / len(features)),
                "silhouette_score": float(silhouette_score(features, labels)),
                "davies_bouldin_score": float(davies_bouldin_score(features, labels)),
                "calinski_harabasz_score": float(
                    calinski_harabasz_score(features, labels)
                ),
                "smallest_cluster_intervals": int(sizes.min()),
                "smallest_cluster_percentage": 100 * float(sizes.min()) / len(features),
                "largest_cluster_intervals": int(sizes.max()),
                "largest_cluster_percentage": 100 * float(sizes.max()) / len(features),
                "clusters_below_one_percent": int(
                    (sizes < 0.01 * len(features)).sum()
                ),
                "random_state": random_state,
                "n_init": KMEANS_N_INIT,
            }
        )

        for cluster_label, cluster_size in enumerate(sizes, start=1):
            cluster_size_rows.append(
                {
                    "k_clusters": cluster_count,
                    "cluster_label": cluster_label,
                    "intervals": int(cluster_size),
                    "percentage_of_clustered_intervals": (
                        100 * float(cluster_size) / len(features)
                    ),
                }
            )

    comparison = pd.DataFrame(comparison_rows)
    comparison_file.unlink(missing_ok=True)
    cluster_sizes_file.unlink(missing_ok=True)
    comparison.to_csv(comparison_file, index=False)
    pd.DataFrame(cluster_size_rows).to_csv(cluster_sizes_file, index=False)
    return comparison


def choose_cluster_count(
    comparison: pd.DataFrame,
    selection_file: Path,
    tolerance: float = KMEANS_SILHOUETTE_TOLERANCE,
    minimum_cluster_share: float = KMEANS_MINIMUM_CLUSTER_SHARE,
    fixed_cluster_count: int | None = FIXED_CLUSTER_COUNT,
) -> dict[str, object]:
    """Choose K from the Step 5 comparison using a fixed, reproducible rule.

    The same table always produces the same K, so a different set of drill holes
    can run end to end without anyone reading the comparison first.

    The rule has two parts. A cluster count is eligible only if none of its
    clusters is smaller than ``minimum_cluster_share`` of the clustered rows.
    Among eligible counts, the chosen K is the smallest whose silhouette score
    is within ``tolerance`` of the best eligible score. Preferring the simplest
    solution that is statistically as good as the best stops the choice drifting
    towards ever more, ever smaller clusters.

    The decision is written out with the numbers behind it, including how many
    other cluster counts were statistically tied. A tie involving several counts
    means the data does not strongly prefer any of them, which is a result worth
    reporting rather than hiding behind a single number.

    Choosing K is a judgement this rule automates; it does not make the answer
    geologically correct. The mineral profiles still need review, and
    ``fixed_cluster_count`` exists so that review can overrule the rule.
    """

    required = {
        "k_clusters",
        "silhouette_score",
        "smallest_cluster_percentage",
        "davies_bouldin_score",
    }
    missing = sorted(required - set(comparison.columns))
    if missing:
        raise ValueError(f"K comparison is missing columns: {', '.join(missing)}")
    if comparison.empty:
        raise ValueError("K comparison contains no rows")
    if not 0 <= tolerance < 1:
        raise ValueError("The silhouette tolerance must be between 0 and 1")

    table = comparison.sort_values("k_clusters", ignore_index=True)
    tested = table["k_clusters"].astype(int)

    # A cluster holding less than the minimum share is treated as a sliver.
    eligible = table.loc[
        table["smallest_cluster_percentage"].ge(100 * minimum_cluster_share)
    ]
    rejected_for_small_clusters = sorted(
        set(tested) - set(eligible["k_clusters"].astype(int))
    )
    if eligible.empty:
        # Never fail outright: report that the guard removed every option and
        # fall back to the full table so the pipeline still produces a result.
        eligible = table
        guard_applied = False
    else:
        guard_applied = True

    best = eligible.loc[eligible["silhouette_score"].idxmax()]
    best_score = float(best["silhouette_score"])
    threshold = best_score - tolerance
    tied = eligible.loc[eligible["silhouette_score"].ge(threshold)]
    chosen = tied.loc[tied["k_clusters"].idxmin()]

    chosen_k = int(chosen["k_clusters"])
    if fixed_cluster_count is not None:
        if fixed_cluster_count not in set(tested):
            raise ValueError(
                f"FIXED_CLUSTER_COUNT={fixed_cluster_count} was not tested; "
                f"tested {tested.min()} to {tested.max()}"
            )
        chosen_k = int(fixed_cluster_count)

    chosen_row = table.loc[tested.eq(chosen_k)].iloc[0]
    decision: dict[str, object] = {
        "chosen_k": chosen_k,
        "chosen_by": "fixed_cluster_count" if fixed_cluster_count is not None
        else "smallest_k_within_tolerance_of_best_silhouette",
        "chosen_silhouette": float(chosen_row["silhouette_score"]),
        "chosen_davies_bouldin": float(chosen_row["davies_bouldin_score"]),
        "chosen_smallest_cluster_percentage": float(
            chosen_row["smallest_cluster_percentage"]
        ),
        "highest_silhouette_k": int(best["k_clusters"]),
        "highest_silhouette": best_score,
        "silhouette_given_up_vs_best": best_score
        - float(chosen_row["silhouette_score"]),
        "tolerance": tolerance,
        "statistically_tied_k_values": ", ".join(
            str(int(value)) for value in sorted(tied["k_clusters"].astype(int))
        ),
        "tied_k_count": int(len(tied)),
        "minimum_cluster_share": minimum_cluster_share,
        "small_cluster_guard_applied": guard_applied,
        "k_rejected_for_small_clusters": ", ".join(
            str(value) for value in rejected_for_small_clusters
        ),
        "k_values_tested": f"{int(tested.min())} to {int(tested.max())}",
    }

    # A wide tie means several cluster counts fit the data about equally well.
    decision["choice_is_marginal"] = bool(decision["tied_k_count"] > 3)

    selection_file.unlink(missing_ok=True)
    pd.DataFrame([decision]).to_csv(selection_file, index=False)
    return decision


def fit_final_clusters(
    interval_file: Path,
    mineral_summary_file: Path,
    cluster_count: int,
    centres_file: Path,
    profiles_file: Path,
    labelled_file: Path,
    minimum_source_rows: int = MINIMUM_INTERVAL_SOURCE_ROWS,
    random_state: int = KMEANS_RANDOM_STATE,
) -> dict[str, object]:
    """Fit the chosen number of clusters and save the model, not only the labels.

    Three files are written:

    * the cluster centres, which ARE the model. Each centre is the average
      mineral mixture of one cluster, expressed in the same 44 columns as the
      data. Saving them means a hole measured later can be assigned to these
      same clusters without refitting, so results stay comparable over time.
    * a readable profile naming each cluster by its leading minerals.
    * every clustered interval with its cluster and its distance from that
      cluster's centre.

    K-means numbers its clusters arbitrarily, so the clusters are renumbered
    here from largest to smallest. That makes the numbering reproducible for a
    given fit. It does NOT make cluster 3 mean the same thing after refitting on
    different holes; only reusing the saved centres does that.

    The distance to the centre is kept because it measures how typical an
    interval is. A large distance marks an interval that fits its cluster
    poorly, which is the basis for flagging unusual depths later.

    The interval file is read only and no interval is edited.
    """

    from sklearn.cluster import KMeans

    if not interval_file.is_file():
        raise FileNotFoundError(f"Missing interval abundance file: {interval_file}")
    if cluster_count < 2:
        raise ValueError("At least two clusters are required")

    intervals = pd.read_csv(interval_file)
    abundance_columns = [
        column
        for column in intervals.columns
        if column.startswith("abundance_") and column != "abundance_sum_check"
    ]
    if not abundance_columns:
        raise ValueError(f"{interval_file}: no mineral-abundance columns found")

    # Use exactly the same rows Step 5 scored, so the chosen K matches the data
    # it was chosen on.
    clustered = intervals.loc[
        intervals["source_rows"].ge(minimum_source_rows)
    ].reset_index(drop=True)
    features = clustered[abundance_columns].to_numpy(dtype=np.float64)
    if not np.isfinite(features).all():
        raise ValueError(f"{interval_file}: an abundance value is missing or infinite")
    if cluster_count >= len(features):
        raise ValueError(
            f"Cannot fit {cluster_count} clusters to {len(features):,} intervals"
        )

    model = KMeans(
        n_clusters=cluster_count,
        init="k-means++",
        n_init=KMEANS_N_INIT,
        random_state=random_state,
    )
    raw_labels = model.fit_predict(features)
    raw_centres = model.cluster_centers_

    # Renumber from largest cluster to smallest so the IDs do not depend on the
    # arbitrary order in which K-means happened to create them.
    raw_sizes = np.bincount(raw_labels, minlength=cluster_count)
    order = np.argsort(-raw_sizes, kind="stable")
    new_id_of_raw = np.empty(cluster_count, dtype=np.int64)
    new_id_of_raw[order] = np.arange(1, cluster_count + 1)
    labels = new_id_of_raw[raw_labels]
    centres = raw_centres[order]
    sizes = raw_sizes[order]

    # How far each interval sits from its own cluster's centre.
    distances = np.linalg.norm(features - centres[labels - 1], axis=1)

    # Recover the readable mineral names. The summary file maps each abundance
    # column to the label as it was spelled in the source data.
    mineral_name_of_column: dict[str, str] = {}
    if mineral_summary_file.is_file():
        summary = pd.read_csv(mineral_summary_file)
        if {"abundance_column", "mineral_name"}.issubset(summary.columns):
            mineral_name_of_column = (
                summary.set_index("abundance_column")["mineral_name"].to_dict()
            )

    def readable_mineral(column: str) -> str:
        """Return the source spelling of a mineral, or a tidy fallback."""

        if column in mineral_name_of_column:
            return str(mineral_name_of_column[column])
        return column[len("abundance_"):].replace("_", "-").title()

    # Name each cluster from its own centre so the name states what the cluster
    # is made of rather than being assigned by hand.
    names: list[str] = []
    for cluster_index in range(cluster_count):
        centre = centres[cluster_index]
        ranking = np.argsort(centre)[::-1]
        leading = readable_mineral(abundance_columns[int(ranking[0])])
        if centre[ranking[0]] >= CLUSTER_DOMINANT_MINERAL_SHARE:
            names.append(f"{leading}-dominant")
        else:
            parts = [
                readable_mineral(abundance_columns[int(index)])
                for index in ranking[:CLUSTER_NAME_MINERAL_COUNT]
                if centre[index] > 0
            ]
            names.append("Mixed: " + " + ".join(parts) if parts else f"Cluster {cluster_index + 1}")

    # Two clusters can produce the same name when they lead on the same
    # minerals in different proportions. Keep names unique so a name is never
    # mistaken for a cluster ID.
    seen: Counter[str] = Counter()
    unique_names: list[str] = []
    for position, name in enumerate(names, start=1):
        seen[name] += 1
        unique_names.append(name if seen[name] == 1 else f"{name} ({position})")
    names = unique_names

    cluster_ids = list(range(1, cluster_count + 1))
    percentages = 100 * sizes / len(features)
    mean_distances = np.array(
        [float(distances[labels == cluster_id].mean()) for cluster_id in cluster_ids]
    )

    # THE MODEL. Every value needed to assign a future interval to these same
    # clusters is here: the centres, the column order they are stated in, and
    # the preparation settings the centres assume.
    centre_frame = pd.DataFrame(centres, columns=abundance_columns)
    centre_frame.insert(0, "mean_distance_to_centre", mean_distances)
    centre_frame.insert(0, "percentage_of_intervals", percentages)
    centre_frame.insert(0, "intervals", sizes)
    centre_frame.insert(0, "cluster_name", names)
    centre_frame.insert(0, "cluster_id", cluster_ids)
    centre_frame["interval_metres"] = intervals["interval_metres"].iloc[0]
    centre_frame["minimum_source_rows"] = minimum_source_rows
    centre_frame["cluster_count"] = cluster_count
    centre_frame["random_state"] = random_state
    centres_file.unlink(missing_ok=True)
    centre_frame.to_csv(centres_file, index=False)

    # The readable profile: what each cluster is actually made of.
    profile_rows: list[dict[str, object]] = []
    for cluster_index, cluster_id in enumerate(cluster_ids):
        centre = centres[cluster_index]
        ranking = np.argsort(centre)[::-1]
        row: dict[str, object] = {
            "cluster_id": cluster_id,
            "cluster_name": names[cluster_index],
            "intervals": int(sizes[cluster_index]),
            "percentage_of_intervals": float(percentages[cluster_index]),
            "estimated_metres": float(
                sizes[cluster_index] * intervals["interval_metres"].iloc[0]
            ),
            "mean_distance_to_centre": float(mean_distances[cluster_index]),
            "minerals_in_centre": int((centre > 0).sum()),
        }
        for rank, feature_index in enumerate(ranking[:5], start=1):
            row[f"top_{rank}_mineral"] = readable_mineral(
                abundance_columns[int(feature_index)]
            )
            row[f"top_{rank}_mean_abundance"] = float(centre[feature_index])
        profile_rows.append(row)
    profiles_file.unlink(missing_ok=True)
    pd.DataFrame(profile_rows).to_csv(profiles_file, index=False)

    # Every clustered interval, kept beside the hole and depth it came from.
    labelled = clustered[
        [
            "hole_id",
            "interval_start",
            "interval_end",
            "interval_metres",
            "source_rows",
            "minerals_present",
        ]
    ].copy()
    labelled["cluster_id"] = labels
    labelled["cluster_name"] = [names[label - 1] for label in labels]
    labelled["distance_to_centre"] = distances
    labelled_file.unlink(missing_ok=True)
    labelled.to_csv(labelled_file, index=False)

    return {
        "cluster_count": cluster_count,
        "clustered_intervals": len(features),
        "mineral_features_used": len(abundance_columns),
        "inertia": float(model.inertia_),
        "smallest_cluster_intervals": int(sizes.min()),
        "largest_cluster_intervals": int(sizes.max()),
        "distinct_cluster_names": len(set(names)),
        "mean_distance_to_centre": float(distances.mean()),
        "largest_distance_to_centre": float(distances.max()),
    }


def compare_with_dominant_mineral_baseline(
    interval_file: Path,
    labelled_file: Path,
    mineral_summary_file: Path,
    summary_file: Path,
    crosstab_file: Path,
    majority_share: float = CLUSTER_DOMINANT_MINERAL_SHARE,
) -> dict[str, object]:
    """Test the clusters against the simplest thing that could have been done.

    The baseline needs no model at all: put every interval in a group named
    after whichever mineral is largest in it. If the clusters merely reproduce
    that grouping, the modelling has re-derived something already present in the
    data and should be reported as such.

    Agreement is measured with the Adjusted Rand Index, which asks whether the
    two methods place the same intervals together and ignores the names and
    numbers they use. One means identical grouping; zero means unrelated. The
    Normalised Mutual Information is reported beside it as a second view.

    The comparison is also split. An interval whose largest mineral reaches
    ``majority_share`` of the mixture has an obvious answer, and the baseline
    should do well there. An interval with no such mineral is one the baseline
    labels on a weak majority. Reporting both separately shows whether the
    clusters earn their place on genuinely mixed rock, which is the claim worth
    making rather than a single overall number.

    Both input files are read only.
    """

    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    for required_file in (interval_file, labelled_file):
        if not required_file.is_file():
            raise FileNotFoundError(f"Missing comparison input: {required_file}")

    intervals = pd.read_csv(interval_file)
    labelled = pd.read_csv(labelled_file)
    abundance_columns = [
        column
        for column in intervals.columns
        if column.startswith("abundance_") and column != "abundance_sum_check"
    ]
    if not abundance_columns:
        raise ValueError(f"{interval_file}: no mineral-abundance columns found")

    # Join on the interval's identity rather than trusting row order.
    key = ["hole_id", "interval_start"]
    merged = labelled[[*key, "cluster_id", "cluster_name"]].merge(
        intervals[[*key, *abundance_columns]], on=key, how="left", validate="one_to_one"
    )
    if len(merged) != len(labelled):
        raise ValueError("Baseline comparison did not match every labelled interval")
    values = merged[abundance_columns].to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("Baseline comparison found a missing abundance value")

    mineral_name_of_column: dict[str, str] = {}
    if mineral_summary_file.is_file():
        summary = pd.read_csv(mineral_summary_file)
        if {"abundance_column", "mineral_name"}.issubset(summary.columns):
            mineral_name_of_column = (
                summary.set_index("abundance_column")["mineral_name"].to_dict()
            )

    def readable_mineral(column: str) -> str:
        if column in mineral_name_of_column:
            return str(mineral_name_of_column[column])
        return column[len("abundance_"):].replace("_", "-").title()

    # THE BASELINE: the single largest mineral in each interval.
    dominant_index = values.argmax(axis=1)
    dominant_share = values.max(axis=1)
    merged["dominant_mineral"] = [
        readable_mineral(abundance_columns[int(index)]) for index in dominant_index
    ]
    merged["dominant_share"] = dominant_share

    cluster_labels = merged["cluster_id"].to_numpy()
    baseline_labels = dominant_index

    has_majority = dominant_share >= majority_share

    def scores(mask: np.ndarray) -> tuple[float, float, int]:
        """Return ARI, NMI and group count for the selected intervals."""

        if int(mask.sum()) < 2:
            return float("nan"), float("nan"), 0
        first = cluster_labels[mask]
        second = baseline_labels[mask]
        if len(set(first)) < 2 or len(set(second)) < 2:
            return float("nan"), float("nan"), len(set(second))
        return (
            float(adjusted_rand_score(first, second)),
            float(normalized_mutual_info_score(first, second)),
            len(set(second)),
        )

    overall_ari, overall_nmi, baseline_groups = scores(
        np.ones(len(merged), dtype=bool)
    )
    majority_ari, majority_nmi, _ = scores(has_majority)
    mixed_ari, mixed_nmi, _ = scores(~has_majority)

    # How often a cluster's own members agree on their largest mineral. A low
    # value means the cluster groups intervals the baseline would separate.
    modal_agreement = (
        merged.groupby("cluster_id")["dominant_mineral"]
        .agg(lambda names: names.value_counts().iloc[0] / len(names))
        .mean()
    )

    decision: dict[str, object] = {
        "intervals": len(merged),
        "clusters": int(merged["cluster_id"].nunique()),
        "baseline_groups": baseline_groups,
        "baseline_rule": "group by the single largest mineral in the interval",
        "adjusted_rand_index": overall_ari,
        "normalised_mutual_information": overall_nmi,
        "majority_share_threshold": majority_share,
        "intervals_with_majority_mineral": int(has_majority.sum()),
        "intervals_without_majority_mineral": int((~has_majority).sum()),
        "adjusted_rand_index_majority_intervals": majority_ari,
        "adjusted_rand_index_mixed_intervals": mixed_ari,
        "normalised_mutual_information_majority_intervals": majority_nmi,
        "normalised_mutual_information_mixed_intervals": mixed_nmi,
        "mean_within_cluster_dominant_mineral_agreement": float(modal_agreement),
    }

    # State the conclusion in the file, so the report does not depend on someone
    # remembering how to read an Adjusted Rand Index.
    if not np.isfinite(overall_ari):
        verdict = "not_comparable"
    elif overall_ari >= 0.85:
        verdict = "clusters_largely_reproduce_the_baseline"
    elif overall_ari >= 0.6:
        verdict = "clusters_mostly_follow_the_baseline_with_real_differences"
    else:
        verdict = "clusters_group_intervals_the_baseline_separates"
    decision["verdict"] = verdict

    summary_file.unlink(missing_ok=True)
    pd.DataFrame([decision]).to_csv(summary_file, index=False)

    # Where exactly the two methods agree and differ, cluster by cluster.
    crosstab = (
        merged.groupby(["cluster_id", "cluster_name", "dominant_mineral"])
        .size()
        .rename("intervals")
        .reset_index()
    )
    cluster_totals = crosstab.groupby("cluster_id")["intervals"].transform("sum")
    crosstab["percentage_of_cluster"] = 100 * crosstab["intervals"] / cluster_totals
    crosstab = crosstab.sort_values(
        ["cluster_id", "intervals"], ascending=[True, False], ignore_index=True
    )
    crosstab_file.unlink(missing_ok=True)
    crosstab.to_csv(crosstab_file, index=False)
    return decision


def create_hole_fingerprints(
    labelled_file: Path,
    cluster_profiles_file: Path,
    fingerprint_file: Path,
    expected_holes: list[str],
) -> pd.DataFrame:
    """Describe each drill hole by the share of its core in each cluster.

    A fingerprint is the percentage of a hole's clustered intervals that fall in
    each cluster. Because every interval covers the same length of core, that
    percentage is also the share of measured metres, so a long hole and a short
    one can be compared directly without any further weighting.

    Counts and metres are carried alongside for auditing. The depth range is
    recorded as context; neither depth nor hole length takes part in the
    comparison that follows.

    The labelled interval file is read only.
    """

    for required_file in (labelled_file, cluster_profiles_file):
        if not required_file.is_file():
            raise FileNotFoundError(f"Missing fingerprint input: {required_file}")

    labelled = pd.read_csv(labelled_file)
    profiles = pd.read_csv(cluster_profiles_file)
    cluster_ids = sorted(int(value) for value in profiles["cluster_id"])
    cluster_names = (
        profiles.set_index("cluster_id")["cluster_name"].astype(str).to_dict()
    )
    unexpected = sorted(set(labelled["cluster_id"].astype(int)) - set(cluster_ids))
    if unexpected:
        raise ValueError(f"Labelled intervals contain unknown clusters: {unexpected}")

    missing_holes = sorted(set(expected_holes) - set(labelled["hole_id"].astype(str)))
    if missing_holes:
        raise ValueError(
            "These drill holes have no clustered intervals: " + ", ".join(missing_holes)
        )

    width = max(2, len(str(max(cluster_ids))))
    counts = (
        labelled.pivot_table(
            index="hole_id",
            columns="cluster_id",
            values="interval_start",
            aggfunc="count",
            fill_value=0,
        )
        .reindex(columns=cluster_ids, fill_value=0)
        .reindex(index=expected_holes)
    )
    if counts.isna().any().any():
        raise ValueError("Fingerprint counts are incomplete for some drill hole")

    totals = counts.sum(axis=1)
    if int(totals.sum()) != len(labelled):
        raise ValueError("Fingerprint totals do not match the labelled intervals")
    percentages = 100 * counts.div(totals, axis=0)

    interval_metres = float(labelled["interval_metres"].iloc[0])
    depths = labelled.groupby("hole_id").agg(
        shallowest=("interval_start", "min"), deepest=("interval_end", "max")
    )
    dominant_position = percentages.to_numpy().argmax(axis=1)
    dominant_ids = [cluster_ids[position] for position in dominant_position]

    fingerprints = pd.DataFrame(
        {
            "hole_id": counts.index.astype(str),
            "clustered_intervals": totals.to_numpy(),
            "clustered_metres": totals.to_numpy() * interval_metres,
            "shallowest_clustered_depth": depths.loc[counts.index, "shallowest"].to_numpy(),
            "deepest_clustered_depth": depths.loc[counts.index, "deepest"].to_numpy(),
            "dominant_cluster_id": dominant_ids,
            "dominant_cluster_name": [cluster_names[value] for value in dominant_ids],
            "dominant_cluster_percentage": percentages.to_numpy().max(axis=1),
        }
    )
    for cluster_id in cluster_ids:
        fingerprints[f"cluster_{cluster_id:0{width}d}_intervals"] = (
            counts[cluster_id].to_numpy()
        )
        fingerprints[f"cluster_{cluster_id:0{width}d}_percentage"] = (
            percentages[cluster_id].to_numpy()
        )
    fingerprints["cluster_percentage_sum_check"] = percentages.sum(axis=1).to_numpy()

    if not np.allclose(
        fingerprints["cluster_percentage_sum_check"], 100, rtol=0, atol=1e-9
    ):
        raise ValueError("A hole's cluster percentages do not total 100")

    fingerprint_file.unlink(missing_ok=True)
    fingerprints.to_csv(fingerprint_file, index=False)
    return fingerprints


def compare_hole_fingerprints(
    fingerprint_file: Path,
    pairwise_file: Path,
    matrix_file: Path,
    closest_file: Path,
) -> dict[str, object]:
    """Compare every pair of drill holes by their cluster fingerprints.

    The Jensen-Shannon distance compares two sets of proportions and copes with
    clusters that are absent from a hole, which a ratio-based measure cannot. It
    runs from zero for identical fingerprints to one for completely separate
    ones. The report states ``100 * (1 - distance)`` so that a larger number
    means a closer match, and carries cosine similarity as a second view.

    Hole length, depth and identity take no part in the calculation: two holes
    match because they contain similar proportions of the same rock types, not
    because they are similar depths or near each other.
    """

    if not fingerprint_file.is_file():
        raise FileNotFoundError(f"Missing fingerprint file: {fingerprint_file}")

    fingerprints = pd.read_csv(fingerprint_file)
    percentage_columns = [
        column
        for column in fingerprints.columns
        if re.fullmatch(r"cluster_\d+_percentage", column)
    ]
    if not percentage_columns:
        raise ValueError(f"{fingerprint_file}: no cluster percentage columns found")

    hole_ids = fingerprints["hole_id"].astype(str).tolist()
    percentages = fingerprints[percentage_columns].to_numpy(dtype=float)
    if not np.isfinite(percentages).all() or (percentages < 0).any():
        raise ValueError("Hole fingerprints contain invalid percentages")

    distributions = percentages / percentages.sum(axis=1, keepdims=True)
    dominant_names = dict(
        zip(hole_ids, fingerprints["dominant_cluster_name"].astype(str))
    )
    dominant_ids = dict(zip(hole_ids, fingerprints["dominant_cluster_id"].astype(int)))

    def jensen_shannon_distance(first: np.ndarray, second: np.ndarray) -> float:
        """Return the base-2 Jensen-Shannon distance between two fingerprints."""

        midpoint = 0.5 * (first + second)
        first_positive = first > 0
        second_positive = second > 0
        first_part = float(
            np.sum(
                first[first_positive]
                * np.log2(first[first_positive] / midpoint[first_positive])
            )
        )
        second_part = float(
            np.sum(
                second[second_positive]
                * np.log2(second[second_positive] / midpoint[second_positive])
            )
        )
        return float(np.sqrt(max(0.5 * (first_part + second_part), 0.0)))

    hole_count = len(hole_ids)
    similarity = np.eye(hole_count, dtype=float) * 100
    pairwise_rows: list[dict[str, object]] = []

    for first_index in range(hole_count):
        for second_index in range(first_index + 1, hole_count):
            first = distributions[first_index]
            second = distributions[second_index]
            distance = jensen_shannon_distance(first, second)
            score = 100 * (1 - distance)
            cosine = float(
                np.dot(first, second)
                / (np.linalg.norm(first) * np.linalg.norm(second))
            )
            similarity[first_index, second_index] = score
            similarity[second_index, first_index] = score
            first_hole = hole_ids[first_index]
            second_hole = hole_ids[second_index]
            pairwise_rows.append(
                {
                    "hole_a": first_hole,
                    "hole_b": second_hole,
                    "fingerprint_similarity_percentage": score,
                    "jensen_shannon_distance": distance,
                    "cosine_similarity_percentage": 100 * cosine,
                    "same_dominant_cluster": dominant_ids[first_hole]
                    == dominant_ids[second_hole],
                    "hole_a_dominant_cluster": dominant_names[first_hole],
                    "hole_b_dominant_cluster": dominant_names[second_hole],
                }
            )

    pairwise = pd.DataFrame(pairwise_rows).sort_values(
        ["fingerprint_similarity_percentage", "hole_a", "hole_b"],
        ascending=[False, True, True],
        ignore_index=True,
    )
    expected_pairs = hole_count * (hole_count - 1) // 2
    if len(pairwise) != expected_pairs:
        raise ValueError("Pairwise comparison did not cover every unique hole pair")

    matrix = pd.DataFrame(similarity, index=hole_ids, columns=hole_ids)
    matrix.index.name = "hole_id"

    # The closest different hole for each hole. The diagonal is a hole's perfect
    # match with itself, so it is removed before choosing.
    closest_rows: list[dict[str, object]] = []
    for position, hole_id in enumerate(hole_ids):
        scores = similarity[position].copy()
        scores[position] = -np.inf
        best = int(np.argmax(scores))
        closest_rows.append(
            {
                "hole_id": hole_id,
                "closest_hole_id": hole_ids[best],
                "fingerprint_similarity_percentage": float(scores[best]),
                "hole_dominant_cluster": dominant_names[hole_id],
                "closest_hole_dominant_cluster": dominant_names[hole_ids[best]],
                "same_dominant_cluster": dominant_ids[hole_id]
                == dominant_ids[hole_ids[best]],
            }
        )
    closest = pd.DataFrame(closest_rows)

    for output_file in (pairwise_file, matrix_file, closest_file):
        output_file.unlink(missing_ok=True)
    pairwise.to_csv(pairwise_file, index=False)
    matrix.to_csv(matrix_file)
    closest.to_csv(closest_file, index=False)

    return {
        "holes": hole_count,
        "unique_pairs": len(pairwise),
        "clusters_compared": len(percentage_columns),
        "most_similar_pair": f"{pairwise.iloc[0]['hole_a']} + "
        f"{pairwise.iloc[0]['hole_b']}",
        "highest_similarity_percentage": float(
            pairwise.iloc[0]["fingerprint_similarity_percentage"]
        ),
        "least_similar_pair": f"{pairwise.iloc[-1]['hole_a']} + "
        f"{pairwise.iloc[-1]['hole_b']}",
        "lowest_similarity_percentage": float(
            pairwise.iloc[-1]["fingerprint_similarity_percentage"]
        ),
        "pairs_sharing_a_dominant_cluster": int(pairwise["same_dominant_cluster"].sum()),
    }


def flag_unusual_intervals(
    interval_file: Path,
    labelled_file: Path,
    mineral_summary_file: Path,
    odd_intervals_file: Path,
    hole_summary_file: Path,
    flag_percentile: float = ODD_INTERVAL_FLAG_PERCENTILE,
) -> dict[str, object]:
    """Rank every interval by how poorly it fits its own cluster.

    Clustering places every interval somewhere, including the ones that match
    nothing well. This step measures that fit so the poor matches can be found
    instead of disappearing into whichever cluster happened to be nearest.

    Fit is the distance from the interval to its cluster's centre, divided by the
    median distance inside that same cluster. Dividing matters: a broad cluster
    has larger distances throughout, so a raw distance would rank all of its
    members as unusual and never flag anything inside a tight cluster. The ratio
    asks whether an interval is unusual *for its own cluster*.

    Nothing is removed and no cluster label changes. The output is a review list:
    the metres of core a geologist should look at first, with the minerals that
    make each one unusual.
    """

    for required_file in (interval_file, labelled_file):
        if not required_file.is_file():
            raise FileNotFoundError(f"Missing input for flagging: {required_file}")
    if not 0 < flag_percentile < 100:
        raise ValueError("The flag percentile must be between 0 and 100")

    labelled = pd.read_csv(labelled_file)
    intervals = pd.read_csv(interval_file)
    abundance_columns = [
        column
        for column in intervals.columns
        if column.startswith("abundance_") and column != "abundance_sum_check"
    ]

    key = ["hole_id", "interval_start"]
    merged = labelled.merge(
        intervals[[*key, *abundance_columns]], on=key, how="left", validate="one_to_one"
    )
    if len(merged) != len(labelled):
        raise ValueError("Flagging did not match every labelled interval")

    # Compare each interval with the spread of its own cluster.
    cluster_median = merged.groupby("cluster_id")["distance_to_centre"].transform(
        "median"
    )
    if (cluster_median <= 0).any():
        raise ValueError("A cluster has a zero median distance; cannot form a ratio")
    merged["cluster_median_distance"] = cluster_median
    merged["oddness_ratio"] = merged["distance_to_centre"] / cluster_median
    merged["percentile_within_cluster"] = (
        merged.groupby("cluster_id")["distance_to_centre"].rank(pct=True) * 100
    )

    threshold = float(np.percentile(merged["oddness_ratio"], flag_percentile))
    merged["flagged_unusual"] = merged["oddness_ratio"].ge(threshold)
    merged["oddness_rank"] = (
        merged["oddness_ratio"].rank(ascending=False, method="min").astype(int)
    )

    mineral_name_of_column: dict[str, str] = {}
    if mineral_summary_file.is_file():
        summary = pd.read_csv(mineral_summary_file)
        if {"abundance_column", "mineral_name"}.issubset(summary.columns):
            mineral_name_of_column = (
                summary.set_index("abundance_column")["mineral_name"].to_dict()
            )

    def readable_mineral(column: str) -> str:
        if column in mineral_name_of_column:
            return str(mineral_name_of_column[column])
        return column[len("abundance_"):].replace("_", "-").title()

    # Name what each flagged interval actually contains, so the list can be acted
    # on without opening the 44-column table to find out why it stood out.
    flagged = merged.loc[merged["flagged_unusual"]].copy()
    values = flagged[abundance_columns].to_numpy(dtype=np.float64)
    order = np.argsort(-values, axis=1)
    for rank in range(3):
        index = order[:, rank]
        flagged[f"top_{rank + 1}_mineral"] = [
            readable_mineral(abundance_columns[int(column)]) for column in index
        ]
        flagged[f"top_{rank + 1}_abundance"] = values[
            np.arange(len(values)), index
        ].round(4)

    report_columns = [
        "oddness_rank", "hole_id", "interval_start", "interval_end",
        "cluster_id", "cluster_name", "minerals_present", "source_rows",
        "distance_to_centre", "cluster_median_distance", "oddness_ratio",
        "percentile_within_cluster",
        "top_1_mineral", "top_1_abundance",
        "top_2_mineral", "top_2_abundance",
        "top_3_mineral", "top_3_abundance",
    ]
    flagged = flagged[report_columns].sort_values("oddness_rank", ignore_index=True)
    odd_intervals_file.unlink(missing_ok=True)
    flagged.to_csv(odd_intervals_file, index=False)

    # Which holes carry the unusual core, so a region can be prioritised.
    per_hole = (
        merged.groupby("hole_id")
        .agg(
            clustered_intervals=("flagged_unusual", "size"),
            flagged_intervals=("flagged_unusual", "sum"),
            worst_oddness_ratio=("oddness_ratio", "max"),
            mean_oddness_ratio=("oddness_ratio", "mean"),
        )
        .reset_index()
    )
    per_hole["percentage_flagged"] = (
        100 * per_hole["flagged_intervals"] / per_hole["clustered_intervals"]
    )
    per_hole = per_hole.sort_values(
        ["flagged_intervals", "worst_oddness_ratio"], ascending=False, ignore_index=True
    )
    hole_summary_file.unlink(missing_ok=True)
    per_hole.to_csv(hole_summary_file, index=False)

    worst = flagged.iloc[0] if len(flagged) else None
    return {
        "intervals_scored": len(merged),
        "intervals_flagged": int(merged["flagged_unusual"].sum()),
        "flag_percentile": flag_percentile,
        "oddness_ratio_threshold": round(threshold, 4),
        "holes_with_flagged_intervals": int(per_hole["flagged_intervals"].gt(0).sum()),
        "most_flagged_hole": str(per_hole.iloc[0]["hole_id"]) if len(per_hole) else "",
        "most_flagged_count": int(per_hole.iloc[0]["flagged_intervals"]) if len(per_hole) else 0,
        "worst_interval": (
            f"{worst['hole_id']} {worst['interval_start']:g}-{worst['interval_end']:g} m"
            if worst is not None else ""
        ),
        "worst_oddness_ratio": round(float(worst["oddness_ratio"]), 3) if worst is not None else 0.0,
    }


def test_cluster_stability(
    interval_file: Path,
    cluster_count: int,
    stability_file: Path,
    seeds: tuple[int, ...] = STABILITY_SEEDS,
    minimum_source_rows: int = MINIMUM_INTERVAL_SOURCE_ROWS,
) -> dict[str, object]:
    """Check the clusters survive a different random starting point.

    K-means begins from randomly chosen centres and improves them, so a result
    can in principle depend on where the search started rather than on the data.
    The fit is repeated with several seeds and compared with the seed used for
    the published result.

    Every other setting matches ``fit_final_clusters`` exactly. A test run with
    stronger settings than the published model would measure a model nobody uses,
    which is the easiest way to report a reassuring number that means nothing.

    Cluster numbers are arbitrary between runs, so agreement is measured with the
    Adjusted Rand Index, which compares which intervals stay together and ignores
    the labels. Each run is also matched to the reference by greatest overlap so
    cluster sizes can be compared after the numbering difference is removed.

    Nothing is written back to the published clustering; this reports only.
    """

    from scipy.optimize import linear_sum_assignment
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score

    if not interval_file.is_file():
        raise FileNotFoundError(f"Missing interval file: {interval_file}")
    if len(seeds) < 2:
        raise ValueError("At least two seeds are needed to compare runs")

    intervals = pd.read_csv(interval_file)
    abundance_columns = [
        column
        for column in intervals.columns
        if column.startswith("abundance_") and column != "abundance_sum_check"
    ]
    clustered = intervals.loc[intervals["source_rows"].ge(minimum_source_rows)]
    features = clustered[abundance_columns].to_numpy(dtype=np.float64)

    labels_by_seed: dict[int, np.ndarray] = {}
    inertia_by_seed: dict[int, float] = {}
    for seed in seeds:
        model = KMeans(
            n_clusters=cluster_count,
            init="k-means++",
            n_init=KMEANS_N_INIT,
            random_state=seed,
        )
        labels_by_seed[seed] = model.fit_predict(features)
        inertia_by_seed[seed] = float(model.inertia_)

    reference_seed = seeds[0]
    reference = labels_by_seed[reference_seed]
    reference_sizes = np.bincount(reference, minlength=cluster_count)

    rows: list[dict[str, object]] = []
    for seed in seeds:
        labels = labels_by_seed[seed]
        sizes = np.bincount(labels, minlength=cluster_count)

        # Match this run's clusters to the reference by greatest shared membership.
        overlap = np.zeros((cluster_count, cluster_count), dtype=np.int64)
        np.add.at(overlap, (reference, labels), 1)
        reference_index, candidate_index = linear_sum_assignment(-overlap)
        matched_rows = int(overlap[reference_index, candidate_index].sum())
        size_differences = [
            abs(int(sizes[candidate]) - int(reference_sizes[ref]))
            for ref, candidate in zip(reference_index, candidate_index)
        ]

        rows.append({
            "random_seed": seed,
            "reference_seed": reference_seed,
            "is_reference": seed == reference_seed,
            "clusters": cluster_count,
            "intervals": len(features),
            "adjusted_rand_index_vs_reference": float(
                adjusted_rand_score(reference, labels)
            ),
            "matched_interval_agreement_percentage": 100 * matched_rows / len(features),
            "inertia": inertia_by_seed[seed],
            "inertia_difference_percentage_vs_reference": 100
            * (inertia_by_seed[seed] - inertia_by_seed[reference_seed])
            / inertia_by_seed[reference_seed],
            "smallest_cluster_intervals": int(sizes.min()),
            "largest_cluster_intervals": int(sizes.max()),
            "largest_matched_cluster_size_difference": max(size_differences),
            "n_init": KMEANS_N_INIT,
        })

    stability = pd.DataFrame(rows)
    stability_file.unlink(missing_ok=True)
    stability.to_csv(stability_file, index=False)

    others = stability.loc[~stability["is_reference"]]
    minimum_ari = float(others["adjusted_rand_index_vs_reference"].min())
    if minimum_ari >= 0.9:
        verdict = "stable_across_seeds"
    elif minimum_ari >= 0.75:
        verdict = "mostly_stable_with_some_boundary_movement"
    else:
        verdict = "unstable_the_grouping_depends_on_the_starting_point"

    return {
        "seeds_tested": len(seeds),
        "reference_seed": reference_seed,
        "minimum_adjusted_rand_index": round(minimum_ari, 4),
        "mean_adjusted_rand_index": round(
            float(others["adjusted_rand_index_vs_reference"].mean()), 4
        ),
        "minimum_matched_agreement_percentage": round(
            float(others["matched_interval_agreement_percentage"].min()), 2
        ),
        "largest_matched_cluster_size_difference": int(
            others["largest_matched_cluster_size_difference"].max()
        ),
        "verdict": verdict,
    }


def compare_tsas_families(
    results: dict[str, dict[str, object]],
    summary_file: Path,
    hole_comparison_file: Path,
) -> dict[str, object]:
    """Compare the clustering produced by each interpretation.

    The two interpretations are not matched cluster by cluster. They can choose
    different numbers of clusters, and cluster numbers are arbitrary in any
    case, so pairing them up would invent a correspondence the data does not
    provide. What is compared instead is the answers:

    * whether the same metres of core are grouped together, using the Adjusted
      Rand Index, which ignores labels and cluster counts;
    * whether the hole-to-hole similarity ranking changes, which is the result
      the client actually uses;
    * whether the same metres are flagged as unusual.

    The middle one carries the most weight. If the ranking holds, the reported
    conclusion stands whichever interpretation GSWA prefers, and the 6% of
    readings the two disagree about never had to be resolved. If it does not
    hold, that is a real limitation and needs an outside reference such as
    assay chemistry to settle.
    """

    from sklearn.metrics import adjusted_rand_score

    families = list(results)
    if len(families) != 2:
        raise ValueError("Family comparison expects exactly two interpretations")
    first_family, second_family = families
    first, second = results[first_family], results[second_family]

    # Only metres present in both runs can be compared. Each interpretation
    # selects its own usable rows, so the two sets overlap but rarely match.
    key = ["hole_id", "interval_start"]
    first_labels = pd.read_csv(first["labelled_file"], usecols=[*key, "cluster_id"])
    second_labels = pd.read_csv(second["labelled_file"], usecols=[*key, "cluster_id"])
    shared = first_labels.merge(
        second_labels, on=key, how="inner", suffixes=("_first", "_second")
    )
    if shared.empty:
        raise ValueError("The two interpretations share no common intervals")

    grouping_agreement = float(
        adjusted_rand_score(shared["cluster_id_first"], shared["cluster_id_second"])
    )

    # Compare the hole rankings. The matrices are symmetric, so only the upper
    # triangle is used, and each hole's self-similarity is excluded.
    first_matrix = pd.read_csv(first["matrix_file"], index_col=0)
    second_matrix = pd.read_csv(second["matrix_file"], index_col=0)
    shared_holes = [h for h in first_matrix.index if h in second_matrix.index]
    first_matrix = first_matrix.loc[shared_holes, shared_holes]
    second_matrix = second_matrix.loc[shared_holes, shared_holes]
    upper = np.triu_indices(len(shared_holes), k=1)
    first_pairs = first_matrix.to_numpy()[upper]
    second_pairs = second_matrix.to_numpy()[upper]
    similarity_correlation = float(np.corrcoef(first_pairs, second_pairs)[0, 1])
    rank_correlation = float(
        np.corrcoef(
            pd.Series(first_pairs).rank(), pd.Series(second_pairs).rank()
        )[0, 1]
    )

    # Does each hole still pick the same closest partner?
    def closest_partner(matrix: pd.DataFrame) -> dict[str, str]:
        partners: dict[str, str] = {}
        for hole in matrix.index:
            others = matrix.loc[hole].drop(labels=[hole])
            partners[hole] = str(others.idxmax())
        return partners

    first_closest = closest_partner(first_matrix)
    second_closest = closest_partner(second_matrix)
    same_closest = sum(
        first_closest[hole] == second_closest[hole] for hole in shared_holes
    )

    hole_rows = [
        {
            "hole_id": hole,
            f"closest_{first_family}": first_closest[hole],
            f"closest_{second_family}": second_closest[hole],
            "same_closest_hole": first_closest[hole] == second_closest[hole],
            f"similarity_{first_family}": float(
                first_matrix.loc[hole, first_closest[hole]]
            ),
            f"similarity_{second_family}": float(
                second_matrix.loc[hole, second_closest[hole]]
            ),
        }
        for hole in shared_holes
    ]
    hole_comparison = pd.DataFrame(hole_rows)
    hole_comparison_file.unlink(missing_ok=True)
    hole_comparison.to_csv(hole_comparison_file, index=False)

    # Do the two interpretations flag the same unusual core?
    def flagged_set(path: Path) -> set[tuple[str, float]]:
        flagged = pd.read_csv(path, usecols=["hole_id", "interval_start"])
        return set(zip(flagged["hole_id"].astype(str), flagged["interval_start"]))

    first_flagged = flagged_set(first["odd_file"])
    second_flagged = flagged_set(second["odd_file"])
    shared_flagged = first_flagged & second_flagged
    union_flagged = first_flagged | second_flagged
    flag_overlap = len(shared_flagged) / len(union_flagged) if union_flagged else 0.0

    if similarity_correlation >= 0.9 and same_closest >= 0.8 * len(shared_holes):
        verdict = "the hole comparison does not depend on the interpretation"
    elif similarity_correlation >= 0.75:
        verdict = "the hole comparison mostly holds, with some pairs reordered"
    else:
        verdict = "the interpretations disagree on which holes are alike"

    summary: dict[str, object] = {
        "first_interpretation": first_family,
        "second_interpretation": second_family,
        f"clusters_{first_family}": first["chosen_k"],
        f"clusters_{second_family}": second["chosen_k"],
        f"intervals_{first_family}": len(first_labels),
        f"intervals_{second_family}": len(second_labels),
        "intervals_in_both": len(shared),
        "grouping_agreement_adjusted_rand_index": round(grouping_agreement, 4),
        "hole_similarity_correlation": round(similarity_correlation, 4),
        "hole_similarity_rank_correlation": round(rank_correlation, 4),
        "holes_compared": len(shared_holes),
        "holes_with_same_closest_partner": same_closest,
        f"flagged_{first_family}": len(first_flagged),
        f"flagged_{second_family}": len(second_flagged),
        "flagged_by_both": len(shared_flagged),
        "flag_overlap_jaccard": round(flag_overlap, 4),
        "verdict": verdict,
    }
    summary_file.unlink(missing_ok=True)
    pd.DataFrame([summary]).to_csv(summary_file, index=False)
    return summary


def run_family(
    family: str,
    hole_ids: list[str],
    cleaned_folder: Path,
    output_folder: Path,
) -> dict[str, object]:
    """Run every clustering step for one interpretation and return its outputs.

    Each interpretation gets its own files, named after the family, so the two
    runs never overwrite one another and can be compared afterwards.
    """

    columns = family_columns(family)
    prefix = f"{family}"

    def path(name: str) -> Path:
        return output_folder / f"{prefix}_{name}"

    selected_file = path("selected_rows.csv")
    decisions_file = path("row_selection_audit.csv")
    summary_file = path("row_selection_summary.csv")
    abundance_file = path("mineral_abundance_rows.csv")
    abundance_summary_file = path("mineral_abundance_summary.csv")
    interval_file = path(f"mineral_abundance_{INTERVAL_METRES:g}m_intervals.csv")
    excluded_intervals_file = path(f"{INTERVAL_METRES:g}m_intervals_excluded.csv")
    k_comparison_file = path(f"{INTERVAL_METRES:g}m_k_comparison.csv")
    k_cluster_sizes_file = path(f"{INTERVAL_METRES:g}m_k_cluster_sizes.csv")
    k_selection_file = path(f"{INTERVAL_METRES:g}m_k_selection.csv")
    cluster_model_file = path(f"{INTERVAL_METRES:g}m_cluster_model_centres.csv")
    cluster_profiles_file = path(f"{INTERVAL_METRES:g}m_cluster_profiles.csv")
    labelled_intervals_file = path(f"{INTERVAL_METRES:g}m_intervals_with_clusters.csv")
    baseline_summary_file = path(f"{INTERVAL_METRES:g}m_baseline_comparison.csv")
    baseline_crosstab_file = path(f"{INTERVAL_METRES:g}m_baseline_crosstab.csv")
    hole_fingerprint_file = path(f"{INTERVAL_METRES:g}m_hole_fingerprints.csv")
    hole_pairwise_file = path(f"{INTERVAL_METRES:g}m_hole_pairwise_similarity.csv")
    hole_matrix_file = path(f"{INTERVAL_METRES:g}m_hole_similarity_matrix.csv")
    closest_holes_file = path(f"{INTERVAL_METRES:g}m_closest_hole_matches.csv")
    odd_intervals_file = path(f"{INTERVAL_METRES:g}m_unusual_intervals.csv")
    odd_hole_summary_file = path(f"{INTERVAL_METRES:g}m_unusual_intervals_by_hole.csv")
    stability_file = path(f"{INTERVAL_METRES:g}m_cluster_stability.csv")

    print(f"\n{'=' * 70}")
    print(f"INTERPRETATION: {family}")
    print(f"{'=' * 70}")

    for existing in (summary_file, selected_file, decisions_file):
        existing.unlink(missing_ok=True)

    # STEP 1: Keep the rows holding a usable rock interpretation.
    summary_rows: list[dict[str, object]] = []
    write_headers = True
    for hole_id in hole_ids:
        summary_rows.append(
            process_hole(
                hole_id,
                columns,
                cleaned_folder / f"{hole_id}_stsas_utsas_cleaned.csv",
                selected_file,
                decisions_file,
                write_headers,
            )
        )
        write_headers = False
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(summary_file, index=False)
    total_rows = int(summary["total_rows"].sum())
    selected_rows = int(summary["selected_rows"].sum())
    print(f"Selected {selected_rows:,} usable rows of {total_rows:,}.")

    # STEP 2 and 3: Mineral-abundance columns, then a review of the minerals.
    abundance_result = create_mineral_abundance_table(
        selected_file, columns, abundance_file, selected_rows
    )
    print(
        f"Built {abundance_result['mineral_abundance_columns']} mineral columns "
        f"for {abundance_result['rows']:,} rows."
    )
    create_mineral_abundance_summary(
        abundance_file, columns, abundance_summary_file, selected_rows
    )

    # STEP 4: One row per length of core rather than per scanner reading.
    interval_result = create_interval_abundance_table(
        abundance_file, interval_file, hole_ids, selected_rows
    )
    print(
        f"Averaged into {interval_result['interval_rows']:,} intervals of "
        f"{interval_result['interval_metres']:g} m "
        f"({interval_result['mean_minerals_per_interval']} minerals each)."
    )

    # STEP 5 and 6: Test cluster counts, then apply the documented rule.
    k_comparison = compare_cluster_counts(
        interval_file, k_comparison_file, k_cluster_sizes_file, excluded_intervals_file
    )
    decision = choose_cluster_count(k_comparison, k_selection_file)
    chosen_k = int(decision["chosen_k"])
    print(
        f"Chosen K={chosen_k} (silhouette {decision['chosen_silhouette']:.4f}; "
        f"best raw was K={decision['highest_silhouette_k']})."
    )
    if decision["choice_is_marginal"]:
        print(
            f"  NOTE: K={decision['statistically_tied_k_values']} score about "
            f"the same; the data does not strongly prefer one."
        )

    # STEP 7: Fit, name each cluster, and save the centres as a reusable model.
    cluster_result = fit_final_clusters(
        interval_file, abundance_summary_file, chosen_k,
        cluster_model_file, cluster_profiles_file, labelled_intervals_file,
    )
    for profile in pd.read_csv(cluster_profiles_file).itertuples(index=False):
        print(
            f"  {profile.cluster_id:>2}. {profile.cluster_name:<44} "
            f"{profile.intervals:>5,} m ({profile.percentage_of_intervals:5.1f}%)"
        )

    # STEP 8: State what the modelling adds over the obvious alternative.
    baseline = compare_with_dominant_mineral_baseline(
        interval_file, labelled_intervals_file, abundance_summary_file,
        baseline_summary_file, baseline_crosstab_file,
    )
    print(
        f"Baseline agreement: {baseline['adjusted_rand_index']:.4f} overall, "
        f"{baseline['adjusted_rand_index_mixed_intervals']:.4f} on mixed core."
    )

    # STEP 9: One fingerprint per hole, then every pair compared.
    create_hole_fingerprints(
        labelled_intervals_file, cluster_profiles_file, hole_fingerprint_file, hole_ids
    )
    comparison = compare_hole_fingerprints(
        hole_fingerprint_file, hole_pairwise_file, hole_matrix_file, closest_holes_file
    )
    print(
        f"Most alike: {comparison['most_similar_pair']} "
        f"({comparison['highest_similarity_percentage']:.1f}%)."
    )

    # STEP 10 and 11: Unusual core, and whether the grouping is repeatable.
    odd = flag_unusual_intervals(
        interval_file, labelled_intervals_file, abundance_summary_file,
        odd_intervals_file, odd_hole_summary_file,
    )
    stability = test_cluster_stability(interval_file, chosen_k, stability_file)
    print(
        f"Flagged {odd['intervals_flagged']:,} unusual intervals; "
        f"stability {stability['minimum_adjusted_rand_index']} across "
        f"{stability['seeds_tested']} seeds."
    )

    return {
        "family": family,
        "chosen_k": chosen_k,
        "selected_rows": selected_rows,
        "interval_rows": interval_result["interval_rows"],
        "labelled_file": labelled_intervals_file,
        "matrix_file": hole_matrix_file,
        "odd_file": odd_intervals_file,
        "profiles_file": cluster_profiles_file,
        "most_similar_pair": comparison["most_similar_pair"],
        "baseline_ari": baseline["adjusted_rand_index"],
        "stability_ari": stability["minimum_adjusted_rand_index"],
        "clusters": cluster_result["cluster_count"],
    }


def main() -> None:
    """Cluster both TSAS interpretations and compare what they conclude."""

    repository = Path(__file__).resolve().parents[1]
    data_root = repository.parent
    selection_file = repository / "configs" / "selections" / "team_raw_tsg_20.yaml"
    cleaned_folder = data_root / "prepared" / "tsas_cleaned"
    output_folder = data_root / "prepared" / "tsas_clustering"
    output_folder.mkdir(parents=True, exist_ok=True)

    hole_ids = read_selected_holes(selection_file)

    # Both interpretations run through identical code with identical settings,
    # so any difference in the result comes from the data rather than from the
    # method applied to it.
    results: dict[str, dict[str, object]] = {}
    for family in TSAS_FAMILIES:
        results[family] = run_family(family, hole_ids, cleaned_folder, output_folder)

    # STEP 12: Does the choice of interpretation change the conclusion?
    print(f"\n{'=' * 70}")
    print("COMPARING THE TWO INTERPRETATIONS")
    print(f"{'=' * 70}")
    comparison = compare_tsas_families(
        results,
        output_folder / f"tsas_family_comparison_{INTERVAL_METRES:g}m.csv",
        output_folder / f"tsas_family_hole_comparison_{INTERVAL_METRES:g}m.csv",
    )
    first, second = TSAS_FAMILIES
    print(
        f"Clusters chosen: {first} K={comparison[f'clusters_{first}']}, "
        f"{second} K={comparison[f'clusters_{second}']}."
    )
    print(
        f"Same metres grouped together: "
        f"{comparison['grouping_agreement_adjusted_rand_index']} "
        f"(over {comparison['intervals_in_both']:,} shared intervals)."
    )
    print(
        f"Hole similarity agreement: "
        f"{comparison['hole_similarity_correlation']} correlation across "
        f"{comparison['holes_compared'] * (comparison['holes_compared'] - 1) // 2} pairs."
    )
    print(
        f"Holes keeping the same closest partner: "
        f"{comparison['holes_with_same_closest_partner']} of "
        f"{comparison['holes_compared']}."
    )
    print(
        f"Unusual core flagged by both: {comparison['flagged_by_both']} "
        f"(overlap {comparison['flag_overlap_jaccard']})."
    )
    print(f"Verdict: {comparison['verdict']}.")


if __name__ == "__main__":
    main()

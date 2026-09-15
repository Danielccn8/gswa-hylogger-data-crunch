"""Extract paired sTSAS and uTSAS SWIR tables from raw TSG packages.

Both interpretations and their depth come from the same downloaded, extracted
TSG package. The script copies the raw values into one comparison CSV per drill
hole. It does not use ``scalar_logs.csv`` and does not clean, filter, replace,
remove, or deduplicate any value or row.
"""

from __future__ import annotations

# argparse allows the data and selection paths to be changed from the command line.
import argparse

# gc releases each large TSG package before the next drill hole is opened.
import gc

# re is used only to read the simple list of holes from the YAML selection file.
import re

# Path provides reliable Windows file and folder handling.
from pathlib import Path

# pandas stores the extracted columns and writes the output CSV files.
import pandas as pd

# pytsg reads the TSG package format used by HyLogger.
from pytsg import parse_tsg


# Map each original TSG scalar name to the consistent output column name.
# These ten fields are the system-level SWIR interpretations used later for cleaning.
STSAS_COLUMN_MAP = {
    "Min1 sTSAS": "min1_s_tsas",
    "Min2 sTSAS": "min2_s_tsas",
    "Min3 sTSAS": "min3_s_tsas",
    "Grp1 sTSAS": "grp1_s_tsas",
    "Grp2 sTSAS": "grp2_s_tsas",
    "Grp3 sTSAS": "grp3_s_tsas",
    "Wt1 sTSAS": "wt1_s_tsas",
    "Wt2 sTSAS": "wt2_s_tsas",
    "Wt3 sTSAS": "wt3_s_tsas",
    "Error sTSAS": "error_s_tsas",
}

# Map the user-level fields from the same raw TSG scalar table.
UTSAS_COLUMN_MAP = {
    "Min1 uTSAS": "min1_u_tsas",
    "Min2 uTSAS": "min2_u_tsas",
    "Min3 uTSAS": "min3_u_tsas",
    "Grp1 uTSAS": "grp1_u_tsas",
    "Grp2 uTSAS": "grp2_u_tsas",
    "Grp3 uTSAS": "grp3_u_tsas",
    "Wt1 uTSAS": "wt1_u_tsas",
    "Wt2 uTSAS": "wt2_u_tsas",
    "Wt3 uTSAS": "wt3_u_tsas",
    "Error uTSAS": "error_u_tsas",
}


def read_selected_holes(selection_file: Path) -> list[str]:
    """Read and validate the drill-hole IDs from the project selection file."""

    # Hole entries in the selection YAML file begin with a hyphen.
    selection_text = selection_file.read_text(encoding="utf-8")
    hole_ids = [
        value.strip().upper()
        for value in re.findall(r"^-\s+(.+?)\s*$", selection_text, re.MULTILINE)
    ]

    # Stop early if the list is empty or contains the same hole more than once.
    if not hole_ids:
        raise ValueError(f"No drill holes were found in {selection_file}")
    if len(hole_ids) != len(set(hole_ids)):
        raise ValueError("The selection contains a repeated drill-hole ID.")

    return hole_ids


def find_tsg_package(data_root: Path, hole_id: str) -> Path:
    """Find the folder containing the hole's NIR/SWIR ``*_tsg.tsg`` file."""

    # Raw packages are extracted below downloads/raw/<hole>/extracted.
    extracted_root = data_root / "downloads" / "raw" / hole_id / "extracted"

    # The SWIR package header ends with _tsg.tsg. Exclude the separate TIR header.
    candidates = sorted(
        path
        for path in extracted_root.rglob("*_tsg.tsg")
        if not path.name.lower().endswith("_tsg_tir.tsg")
    )

    # Every selected hole should resolve to exactly one SWIR TSG package.
    if len(candidates) != 1:
        raise ValueError(
            f"{hole_id}: expected one SWIR TSG package, found {len(candidates)}"
        )

    # pytsg expects the package folder rather than the header-file path.
    return candidates[0].parent


def extract_hole(data_root: Path, output_folder: Path, hole_id: str) -> dict[str, object]:
    """Extract paired sTSAS and uTSAS columns for one hole without removing rows."""

    # Locate and open the downloaded TSG package. CRAS data is unnecessary here.
    package_folder = find_tsg_package(data_root, hole_id)
    package = parse_tsg.read_package(package_folder, read_cras_file=False)

    # In pytsg, the NIR object contains the combined VNIR/SWIR sample information.
    sample_headers = package.nir.sampleheaders.reset_index(drop=True)
    scalar_table = package.nir.scalars.reset_index(drop=True)

    # Depth is stored as D in the TSG sample-header table.
    if "D" not in sample_headers.columns:
        raise ValueError(f"{hole_id}: the TSG package does not contain the D depth field")

    # Require every planned sTSAS field so all 20 output files have the same schema.
    missing_columns = [name for name in STSAS_COLUMN_MAP if name not in scalar_table.columns]
    if missing_columns:
        raise ValueError(
            f"{hole_id}: missing required sTSAS columns: {', '.join(missing_columns)}"
        )

    missing_utsas = [name for name in UTSAS_COLUMN_MAP if name not in scalar_table]
    if missing_utsas:
        raise ValueError(
            f"{hole_id}: missing required uTSAS columns: {', '.join(missing_utsas)}"
        )

    # Scalar and header tables should describe the same measurement rows.
    if len(sample_headers) != len(scalar_table):
        raise ValueError(
            f"{hole_id}: header rows ({len(sample_headers)}) and scalar rows "
            f"({len(scalar_table)}) do not match"
        )
    # TSG values are point measurements, so start and end depth both use D.
    depth = pd.to_numeric(sample_headers["D"], errors="coerce")
    output = pd.DataFrame(
        {
            "hole_id": hole_id,
            "start_depth": depth,
            "end_depth": depth,
        }
    )

    # Copy both interpretation families directly from the same raw scalar table.
    # Raw no-data markers and numeric category codes remain unchanged.
    for source_name, output_name in (STSAS_COLUMN_MAP | UTSAS_COLUMN_MAP).items():
        output[output_name] = scalar_table[source_name]

    # Save a new CSV without changing the downloaded TSG package.
    output_file = output_folder / f"{hole_id}_stsas_utsas.csv"
    output.to_csv(output_file, index=False)

    # Return provenance and row counts for the final extraction report.
    return {
        "hole_id": hole_id,
        "package_folder": str(package_folder),
        "output_file": str(output_file),
        "rows": len(output),
        "stsas_columns": len(STSAS_COLUMN_MAP),
        "utsas_columns": len(UTSAS_COLUMN_MAP),
        "missing_depth_rows": int(depth.isna().sum()),
        "status": "complete",
    }


def main() -> None:
    """Extract sTSAS CSV files for all holes in the 20-hole selection."""

    # This script is stored under <repository>/scripts.
    repository = Path(__file__).resolve().parents[1]

    # Use the shared data root and 20-hole selection by default.
    parser = argparse.ArgumentParser(
        description="Create paired sTSAS and uTSAS SWIR tables without cleaning rows."
    )
    parser.add_argument("--data-root", type=Path, default=repository.parent)
    parser.add_argument(
        "--selection",
        type=Path,
        default=repository / "configs" / "selections" / "team_raw_tsg_20.yaml",
    )
    arguments = parser.parse_args()

    # The folder name records that both column families came from raw extraction.
    output_folder = arguments.data_root / "prepared" / "sathi_tsas_raw_extracted"
    output_folder.mkdir(parents=True, exist_ok=True)

    # Read the selection, extract every hole, and collect a provenance report.
    hole_ids = read_selected_holes(arguments.selection)
    report_rows = []
    for hole_id in hole_ids:
        report_rows.append(
            extract_hole(arguments.data_root, output_folder, hole_id)
        )
        gc.collect()

    # Save one small report proving which source package produced each CSV.
    report_file = output_folder / "sathi_tsas_raw_extraction_report.csv"
    pd.DataFrame(report_rows).to_csv(report_file, index=False)

    print(f"Created paired sTSAS/uTSAS data for {len(report_rows)} holes in {output_folder}")
    print(f"Fetching report: {report_file}")
    print("No rows were cleaned or removed.")


# Run the workflow only when this file is executed directly.
if __name__ == "__main__":
    main()

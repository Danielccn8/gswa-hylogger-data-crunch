"""Produce a data dictionary of scalar columns in a registered HyLogger package.

    python scripts/legacy/dump_scalar_dictionary.py 05GJD001

The TSG file documents itself. Every scalar column is listed in the file's own
band headers, and any column holding categories carries a lookup table naming
the reference library it came from and every category in it. This script reads
those structures and writes them out, so the dictionary is generated from the
data rather than transcribed by hand.

Output: <data-dir>/prepared/dictionaries/<hole>_scalar_dictionary.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from pytsg import parse_tsg

from hylogger.config import AppConfig
from hylogger.raw import find_package_dir

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("hole_id")
parser.add_argument("--package-dir", type=Path)
args = parser.parse_args()

config = AppConfig.load()
package_dir = args.package_dir or find_package_dir(config, args.hole_id)
if package_dir is None:
    raise SystemExit(
        f"No extracted package is registered for {args.hole_id}. "
        f"Run: hylogger raw fetch {args.hole_id} --components spectra"
    )

package = parse_tsg.read_package(package_dir, read_cras_file=False)

rows = []
for label, section in (("NIR/SWIR", package.nir), ("TIR", package.tir)):
    # classes: the lookup tables stored in the file. Each names the reference
    # library that produced it, e.g. "S_SWIR_TSA705 Minerals" with 61 entries.
    lookups = {c.class_number: c for c in section.classes.values()}

    scalars = section.scalars
    for band in section.bandheaders:
        name = band.name
        if name not in scalars.columns:
            continue

        values = scalars[name]
        if pd.api.types.is_numeric_dtype(values):
            values = values.where(values > -1e38)  # TSG's missing-value sentinel
        else:
            values = values.replace("", np.nan)

        lookup = lookups.get(band.class_number)
        rows.append(
            {
                "section": label,
                "column": name,
                "kind": "category" if lookup else "number",
                "reference_library": lookup.name if lookup else "",
                "n_categories": len(lookup.classes) if lookup else "",
                "categories": ", ".join(list(lookup.classes.values())[:8]) if lookup else "",
                "filled_pct": round(values.notna().mean() * 100, 1),
                "distinct": values.nunique(),
            }
        )

dictionary = pd.DataFrame(rows)
output_dir = config.data_dir / "prepared" / "dictionaries"
output_dir.mkdir(parents=True, exist_ok=True)
path = output_dir / f"{args.hole_id.upper()}_scalar_dictionary.csv"
dictionary.to_csv(path, index=False)

print(f"{len(dictionary)} scalar columns documented -> {path}\n")
print("Reference libraries the file declares:")
for label, section in (("NIR/SWIR", package.nir), ("TIR", package.tir)):
    for c in section.classes.values():
        if c.classes:
            print(f"   [{label:8s}] {c.name:28s} {len(c.classes):>4} categories")

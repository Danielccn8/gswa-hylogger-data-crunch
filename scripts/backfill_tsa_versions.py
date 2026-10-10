"""Add system-vs-user TSA comparisons to raw summaries whose spectra are still on disk.

    python scripts/backfill_tsa_versions.py [--data-dir C:/HyLoggerData]

The comparison needs the scalar plane of the .bip file, so only holes whose
extracted package is still present can be backfilled; for the rest the Hole page
offers to download the spectra again. Re-running the raw summary is deterministic,
so existing raw products are rewritten unchanged apart from tsa_versions.parquet.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from hylogger.explorer.db import connect
from hylogger.explorer.raw_summary import summarise_hole


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("HYLOGGER_DATA_DIR", "C:/HyLoggerData")),
    )
    parser.add_argument("--force", action="store_true", help="recompute existing comparisons")
    args = parser.parse_args()
    with connect() as conn:
        busy = {
            r[0]
            for r in conn.execute(
                "SELECT hole_id FROM explorer_hole WHERE raw_status IN ('downloading','fetched')"
            ).fetchall()
        }
    done = failed = skipped = 0
    started = time.perf_counter()
    for hole_dir in sorted((args.data_dir / "downloads" / "raw").iterdir()):
        package = hole_dir / "extracted"
        output = args.data_dir / "products" / "raw" / hole_dir.name
        if hole_dir.name in busy or not any(package.rglob("*_tsg.bip")):
            continue
        if (output / "tsa_versions.parquet").exists() and not args.force:
            skipped += 1
            continue
        try:
            result = summarise_hole(package, hole_dir.name, output)
            done += 1
            print(hole_dir.name, result.get("tsa_versions", {}).keys() or "no paired TSA")
        except Exception as error:  # report and continue with the next hole
            failed += 1
            print(hole_dir.name, "failed:", error)
    print(
        f"backfilled {done}, already done {skipped}, failed {failed} "
        f"in {time.perf_counter() - started:.0f} s"
    )


if __name__ == "__main__":
    main()

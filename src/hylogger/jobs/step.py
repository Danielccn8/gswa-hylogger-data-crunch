"""One acquisition step for one hole, run in a child process by the worker.

    python -m hylogger.jobs.step processed-fetch HOLE
    python -m hylogger.jobs.step raw-fetch HOLE --mode spectral_only
    python -m hylogger.jobs.step raw-summarize HOLE [--discard-source]

The last stdout line is a JSON result. Running each step in its own process
lets the worker enforce a timeout (nvcl-kit applies none) and cancel safely:
downloads resume from their .part files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from ..config import AppConfig


def processed_fetch(config: AppConfig, hole_id: str, args: argparse.Namespace) -> dict:
    from ..processed import fetch_processed_hole

    outputs = fetch_processed_hole(config, hole_id, all_logs=True)
    return {"datasets": outputs}


def raw_fetch(config: AppConfig, hole_id: str, args: argparse.Namespace) -> dict:
    if args.mode == "full_zip":
        from ..raw import download_raw_hole, parse_components

        return download_raw_hole(config, hole_id, components=parse_components("spectra"))
    from ..explorer.spectral_fetch import fetch_spectral

    local = config.data_dir / "downloads" / "raw" / hole_id / "archive" / f"{hole_id}.zip"
    legacy_root = Path(os.environ.get("HYLOGGER_LEGACY_DIR", "/legacy"))
    legacy = legacy_root / "downloads" / "raw" / hole_id / "archive" / f"{hole_id}.zip"
    archive = local if local.exists() else legacy if legacy.exists() else None
    return fetch_spectral(config, hole_id, local_archive=archive)


def raw_summarize(config: AppConfig, hole_id: str, args: argparse.Namespace) -> dict:
    from ..explorer.raw_summary import summarise_hole

    package = config.data_dir / "downloads" / "raw" / hole_id / "extracted"
    if not package.exists() or not any(package.rglob("*_tsg.tsg")):
        raise FileNotFoundError(f"No extracted spectra for {hole_id}; fetch raw first")
    output = config.data_dir / "products" / "raw" / hole_id
    result = summarise_hole(package, hole_id, output)
    summarised = any(v.get("status") == "summarised" for v in result["sensors"].values())
    if args.discard_source and summarised:
        # Keep the .tsg tables (a few MB: system and user TSA, masks, scalars); drop the spectra.
        hole_dir = config.data_dir / "downloads" / "raw" / hole_id
        freed = 0
        for path in list(hole_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() != ".tsg":
                freed += path.stat().st_size
                path.unlink(missing_ok=True)
        shutil.rmtree(hole_dir / "archive", ignore_errors=True)
        result["discarded_source_bytes"] = freed
        result["kept_tables"] = sorted(p.name for p in hole_dir.rglob("*.tsg"))
    result["output"] = str(output)
    return result


STEPS = {
    "processed-fetch": processed_fetch,
    "raw-fetch": raw_fetch,
    "raw-summarize": raw_summarize,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("hole_id")
    parser.add_argument("--mode", choices=["spectral_only", "full_zip"], default="spectral_only")
    parser.add_argument("--discard-source", action="store_true")
    args = parser.parse_args()
    config = AppConfig.load()
    try:
        result = STEPS[args.step](config, args.hole_id.upper(), args)
    except Exception as error:  # reported to the worker as structured JSON
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, default=str))
        sys.exit(1)
    print(json.dumps({"ok": True, "result": result}, default=str))


if __name__ == "__main__":
    main()

"""Fetch processed NVCL logs for many holes with the existing CLI, in parallel.

Each hole runs `hylogger processed fetch` in its own subprocess so a stalled
NVCL request (nvcl-kit applies no timeout) can be killed without losing the
rest of the batch. Completed holes are skipped on re-run.

Example:
    python scripts/bulk_fetch_processed.py --data-dir C:/HyLoggerData --workers 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def fetched(data_dir: Path, hole_id: str) -> bool:
    return any((data_dir / "downloads" / "processed" / hole_id).glob("*/scalar_logs.parquet"))


def known_empty(data_dir: Path, hole_id: str) -> bool:
    """NVCL lists a dataset for the hole but publishes no scalar logs."""
    inventory = data_dir / "downloads" / "processed" / hole_id / "inventory.json"
    if not inventory.exists():
        return False
    datasets = json.loads(inventory.read_text(encoding="utf-8")).get("datasets", [])
    return not any(item.get("logs") for item in datasets)


def fetch_one(data_dir: Path, hole_id: str, timeout: int) -> dict:
    started = time.perf_counter()
    command = [
        sys.executable,
        "-m",
        "hylogger",
        "--data-dir",
        str(data_dir),
        "processed",
        "fetch",
        hole_id,
        "--all-logs",
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        status = "ok" if done.returncode == 0 and fetched(data_dir, hole_id) else "failed"
        tail = ((done.stdout or "") + (done.stderr or ""))[-600:]
    except subprocess.TimeoutExpired:
        status, tail = "timeout", f"killed after {timeout} s"
    return {
        "hole_id": hole_id,
        "status": status,
        "seconds": round(time.perf_counter() - started, 2),
        "detail": tail.strip(),
        "finished_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--holes", nargs="*")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch holes already present")
    args = parser.parse_args()

    catalog = pd.read_parquet(args.data_dir / "catalog" / "catalog.parquet")
    holes = args.holes or sorted(catalog.loc[catalog.nvcl_available.fillna(False), "hole_id"])
    if not args.refresh:
        holes = [
            hole
            for hole in holes
            if not fetched(args.data_dir, hole) and not known_empty(args.data_dir, hole)
        ]
    if args.limit:
        holes = holes[: args.limit]
    log_path = args.data_dir / "logs" / f"processed_bulk_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"{len(holes)} holes to fetch with {args.workers} workers -> {log_path}", flush=True)

    counts: dict[str, int] = {}
    with ThreadPoolExecutor(args.workers) as pool, log_path.open("a", encoding="utf-8") as log:
        futures = [pool.submit(fetch_one, args.data_dir, hole, args.timeout) for hole in holes]
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            log.write(json.dumps(result) + "\n")
            log.flush()
            if index % 25 == 0 or index == len(holes):
                print(f"{index}/{len(holes)} {counts}", flush=True)


if __name__ == "__main__":
    main()

"""Job queue command line.

python -m hylogger.jobs submit processed_fetch '{"holes": ["KEDD002"]}'
python -m hylogger.jobs list [--limit 10]
python -m hylogger.jobs logs JOB_ID
python -m hylogger.jobs cancel JOB_ID
"""

from __future__ import annotations

import argparse
import json

from ..explorer.db import connect, read_frame
from . import queue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("kind", choices=sorted(queue.LANES))
    submit.add_argument("params", nargs="?", default="{}")
    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=10)
    logs = sub.add_parser("logs")
    logs.add_argument("job_id")
    logs.add_argument("--limit", type=int, default=30)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("job_id")
    args = parser.parse_args()
    with connect() as conn:
        if args.command == "submit":
            print(queue.submit(conn, args.kind, json.loads(args.params)))
        elif args.command == "list":
            frame = read_frame(
                conn,
                "SELECT left(job_id::text, 8) AS job, kind, status, progress, "
                "left(coalesce(error, ''), 80) AS error, created_at::time(0) AS created, "
                "(coalesce(finished_at, now()) - started_at)::interval(0) AS runtime "
                "FROM job ORDER BY created_at DESC LIMIT %s",
                (args.limit,),
            )
            print(frame.to_string(index=False) if len(frame) else "No jobs")
        elif args.command == "logs":
            frame = read_frame(
                conn,
                "SELECT ts::time(0) AS ts, level, message FROM job_log "
                "WHERE job_id::text LIKE %s || '%%' ORDER BY id DESC LIMIT %s",
                (args.job_id, args.limit),
            )
            for row in frame[::-1].itertuples():
                print(f"{row.ts} {row.level:7s} {row.message}")
        else:
            print(queue.request_cancel(conn, args.job_id) or "not cancellable")


if __name__ == "__main__":
    main()

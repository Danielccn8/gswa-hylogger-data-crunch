"""Apply platform SQL migrations once, in order, recording each in schema_migrations.

    python platform/db/migrate.py [--database-url URL]

Every file is also written to be idempotent, so databases created by the
earlier init-script mechanism migrate cleanly.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
MIGRATIONS = [
    ("001_schema", HERE / "schema.sql"),
    *sorted((path.stem, path) for path in (HERE / "migrations").glob("*.sql")),
]


def migrate(database_url: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        # Serialise concurrent starters (API and worker) on one advisory lock.
        conn.execute("SELECT pg_advisory_lock(7101)")
        try:
            done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
            for name, path in MIGRATIONS:
                if name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (name,))
                applied.append(name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(7101)")
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL", "postgresql://hylogger:hylogger@127.0.0.1:5434/hylogger"
        ),
    )
    applied = migrate(parser.parse_args().database_url)
    print(f"Applied: {', '.join(applied) or 'none (up to date)'}")


if __name__ == "__main__":
    main()

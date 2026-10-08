"""Small PostgreSQL helpers shared by the explorer loaders, jobs and API."""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath

import numpy as np
import pandas as pd
import psycopg

DEFAULT_URL = "postgresql://hylogger:hylogger@127.0.0.1:5434/hylogger"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def connect(url: str | None = None, **kwargs) -> psycopg.Connection:
    return psycopg.connect(url or database_url(), connect_timeout=10, **kwargs)


def _pg_array(value: object) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    items = list(value)
    escaped = ['"' + str(item).replace("\\", "\\\\").replace('"', '\\"') + '"' for item in items]
    return "{" + ",".join(escaped) + "}"


def copy_frame(
    conn: psycopg.Connection,
    table: str,
    frame: pd.DataFrame,
    *,
    json_columns: Iterable[str] = (),
    array_columns: Iterable[str] = (),
) -> int:
    """Bulk-load a frame with COPY ... CSV. Empty cells load as NULL."""
    if frame.empty:
        return 0
    data = frame.copy()
    for column in json_columns:
        data[column] = data[column].map(
            lambda value: None if value is None else json.dumps(value, default=_json_default)
        )
    for column in array_columns:
        data[column] = data[column].map(_pg_array)
    buffer = io.StringIO()
    data.to_csv(buffer, index=False, header=False, na_rep="")
    buffer.seek(0)
    columns = ",".join(f'"{name}"' for name in data.columns)
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, NULL '')") as copy:
            while chunk := buffer.read(1 << 20):
                copy.write(chunk)
    return len(data)


def _json_default(value: object) -> object:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if np.isnan(value) else float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Not JSON serialisable: {type(value).__name__}")


def read_frame(conn: psycopg.Connection, sql: str, params: object = None) -> pd.DataFrame:
    """Run a query and return a DataFrame (avoids pandas' SQLAlchemy-only warning)."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [column.name for column in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def resolve_product(path: str | None, data_dir) -> Path | None:
    """Map a stored product path (written on the host or in a container) onto data_dir."""
    if not path:
        return None
    pure = PureWindowsPath(path) if ("\\" in path or ":" in path[:3]) else PurePosixPath(path)
    parts = pure.parts
    if "products" in parts:
        return Path(data_dir).joinpath(*parts[parts.index("products") :])
    return Path(path)

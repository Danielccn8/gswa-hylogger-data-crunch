from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .util import utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_key TEXT PRIMARY KEY,
    hole_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    dataset_id TEXT,
    source_url TEXT,
    local_path TEXT,
    status TEXT NOT NULL,
    size_bytes INTEGER,
    sha256 TEXT,
    etag TEXT,
    last_modified TEXT,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS artifacts_hole_idx ON artifacts (hole_id, kind);
CREATE INDEX IF NOT EXISTS artifacts_status_idx ON artifacts (status, kind);
"""


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialise(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA journal_mode=WAL")

    def record(
        self,
        *,
        artifact_key: str,
        hole_id: str,
        kind: str,
        status: str,
        dataset_id: str | None = None,
        source_url: str | None = None,
        local_path: Path | str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        values = {
            "artifact_key": artifact_key,
            "hole_id": hole_id,
            "kind": kind,
            "dataset_id": dataset_id,
            "source_url": source_url,
            "local_path": str(local_path) if local_path is not None else None,
            "status": status,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "etag": etag,
            "last_modified": last_modified,
            "started_at": started_at,
            "completed_at": completed_at,
            "error": error,
            "metadata_json": json.dumps(metadata or {}, default=str),
            "updated_at": utc_now(),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_key, hole_id, kind, dataset_id, source_url, local_path,
                    status, size_bytes, sha256, etag, last_modified, started_at,
                    completed_at, error, metadata_json, updated_at
                ) VALUES (
                    :artifact_key, :hole_id, :kind, :dataset_id, :source_url, :local_path,
                    :status, :size_bytes, :sha256, :etag, :last_modified, :started_at,
                    :completed_at, :error, :metadata_json, :updated_at
                )
                ON CONFLICT(artifact_key) DO UPDATE SET
                    hole_id = excluded.hole_id,
                    kind = excluded.kind,
                    dataset_id = excluded.dataset_id,
                    source_url = COALESCE(excluded.source_url, artifacts.source_url),
                    local_path = COALESCE(excluded.local_path, artifacts.local_path),
                    status = excluded.status,
                    size_bytes = COALESCE(excluded.size_bytes, artifacts.size_bytes),
                    sha256 = COALESCE(excluded.sha256, artifacts.sha256),
                    etag = COALESCE(excluded.etag, artifacts.etag),
                    last_modified = COALESCE(excluded.last_modified, artifacts.last_modified),
                    started_at = COALESCE(excluded.started_at, artifacts.started_at),
                    completed_at = COALESCE(excluded.completed_at, artifacts.completed_at),
                    error = excluded.error,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                values,
            )

    def get(self, artifact_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_key = ?", (artifact_key,)
            ).fetchone()
        return self._decode(row) if row else None

    def find(self, *, hole_id: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[str] = []
        if hole_id:
            clauses.append("hole_id = ?")
            values.append(hole_id)
        if kind:
            clauses.append("kind = ?")
            values.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM artifacts {where} ORDER BY hole_id, kind, dataset_id", values
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        try:
            record["metadata"] = json.loads(record.pop("metadata_json"))
        except json.JSONDecodeError:
            record["metadata"] = {}
            record.pop("metadata_json", None)
        return record

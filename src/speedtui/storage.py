"""Result history in a local SQLite file.

Default location: $XDG_DATA_HOME/speedtui/history.db, which is
~/.local/share/speedtui/history.db on most systems.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from speedtui.measure import SpeedTestResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,      -- ISO 8601, UTC
    colo            TEXT NOT NULL,
    country         TEXT NOT NULL,
    ping_ms         REAL NOT NULL,
    jitter_ms       REAL NOT NULL,
    download_mbps   REAL NOT NULL,
    upload_mbps     REAL NOT NULL,
    streams         INTEGER NOT NULL,
    duration_s      REAL NOT NULL,
    latency_samples TEXT NOT NULL       -- JSON list of ms
)
"""


class StorageError(Exception):
    """History could not be read or written. The message is user-facing."""


@dataclass
class HistoryEntry:
    id: int
    timestamp: datetime
    colo: str
    ping_ms: float
    jitter_ms: float
    download_mbps: float
    upload_mbps: float


def default_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "speedtui" / "history.db"


class History:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()

    def _connect(self) -> sqlite3.Connection:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path)
            conn.execute(SCHEMA)
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(f"Could not open history at {self.path}: {exc}") from exc
        return conn

    def save(self, result: SpeedTestResult, duration_s: float) -> int:
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO results (timestamp, colo, country, ping_ms, jitter_ms,"
                    " download_mbps, upload_mbps, streams, duration_s, latency_samples)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result.timestamp.isoformat(),
                        result.server.colo,
                        result.server.country,
                        result.latency.median_ms,
                        result.latency.jitter_ms,
                        result.download.mbps,
                        result.upload.mbps,
                        result.download.streams,
                        duration_s,
                        json.dumps([round(s, 3) for s in result.latency.samples_ms]),
                    ),
                )
            return int(cursor.lastrowid or 0)
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save the result to {self.path}: {exc}") from exc
        finally:
            conn.close()

    def recent(self, limit: int = 200) -> list[HistoryEntry]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, timestamp, colo, ping_ms, jitter_ms, download_mbps, upload_mbps"
                " FROM results ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not read history from {self.path}: {exc}") from exc
        finally:
            conn.close()
        return [
            HistoryEntry(row[0], datetime.fromisoformat(row[1]), *row[2:])
            for row in rows
        ]

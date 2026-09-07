"""Append-only JSONL and SQLite event persistence."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class EventStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = directory / "events.jsonl"
        self.sqlite_path = directory / "autotrader.sqlite3"
        with closing(sqlite3.connect(self.sqlite_path)) as connection:
            with connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        symbol TEXT,
                        payload_json TEXT NOT NULL
                    )"""
                )

    def record(self, event_type: str, payload: Any, symbol: str | None = None) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        body = _jsonable(payload)
        row = {"timestamp": timestamp, "event_type": event_type, "symbol": symbol, "payload": body}
        serialized = json.dumps(row, ensure_ascii=False, sort_keys=True)
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
        with closing(sqlite3.connect(self.sqlite_path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO events(timestamp, event_type, symbol, payload_json) VALUES (?, ?, ?, ?)",
                    (timestamp, event_type, symbol, json.dumps(body, ensure_ascii=False, sort_keys=True)),
                )

    def accepted_order_times(self, symbol: str) -> tuple[datetime, ...]:
        """Return persisted successful entry times for conservative restart-safe throttling."""
        with closing(sqlite3.connect(self.sqlite_path)) as connection:
            rows = connection.execute(
                "SELECT timestamp, payload_json FROM events WHERE event_type = 'order_result' AND symbol = ?",
                (symbol,),
            ).fetchall()
        result: list[datetime] = []
        for timestamp, payload in rows:
            try:
                if json.loads(payload).get("result", {}).get("accepted"):
                    result.append(datetime.fromisoformat(timestamp))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(result)

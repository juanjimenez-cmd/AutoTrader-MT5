"""Persistent guards that reduce repeated entries into the same market noise."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import EntryControlConfig
from .storage import EventStore


class EntryThrottle:
    def __init__(self, config: EntryControlConfig, store: EventStore):
        self.config = config
        self.store = store

    def evaluate(self, canonical_symbol: str, now: datetime) -> tuple[bool, str]:
        if now.tzinfo is None:
            raise ValueError("entry throttle requires a timezone-aware datetime")
        current = now.astimezone(timezone.utc)
        entries = tuple(item.astimezone(timezone.utc) for item in self.store.accepted_order_times(canonical_symbol))
        if entries:
            latest = max(entries)
            remaining = timedelta(minutes=self.config.cooldown_minutes) - (current - latest)
            if remaining.total_seconds() > 0:
                return False, f"cooldown active for {remaining.total_seconds() / 60:.0f} more minutes"
        today_entries = sum(item.date() == current.date() for item in entries)
        if today_entries >= self.config.max_entries_per_symbol_day:
            return False, (
                f"daily entry limit reached ({today_entries}/{self.config.max_entries_per_symbol_day} UTC day)"
            )
        return True, "entry controls allow a new position"

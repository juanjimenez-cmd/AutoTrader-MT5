"""Conservative, configurable entry windows for weekend market closures."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import SessionConfig


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


class EntrySessionGuard:
    def __init__(self, config: SessionConfig):
        self.config = config
        self._friday_cutoff = _minutes(config.friday_entry_cutoff_utc)
        self._sunday_resume = _minutes(config.sunday_entry_resume_utc)

    def evaluate(self, group: str, now: datetime | None = None) -> tuple[bool, str]:
        if not self.config.weekend_guard_enabled or group not in self.config.guarded_groups:
            return True, "weekend guard does not apply"

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("session guard requires a timezone-aware datetime")
        current = current.astimezone(timezone.utc)
        minute_of_day = current.hour * 60 + current.minute

        blocked = (
            (current.weekday() == 4 and minute_of_day >= self._friday_cutoff)
            or current.weekday() == 5
            or (current.weekday() == 6 and minute_of_day < self._sunday_resume)
        )
        if not blocked:
            return True, "entry window open"
        return (
            False,
            "weekend entry guard: new positions are blocked from "
            f"Friday {self.config.friday_entry_cutoff_utc} UTC until "
            f"Sunday {self.config.sunday_entry_resume_utc} UTC",
        )

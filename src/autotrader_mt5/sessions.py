"""Conservative, configurable entry windows for weekend market closures."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import SessionConfig


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


class EntrySessionGuard:
    def __init__(self, config: SessionConfig):
        self.config = config
        self._friday_cutoff = _minutes(config.friday_entry_cutoff_utc)
        self._sunday_resume = _minutes(config.sunday_entry_resume_utc)
        self._entry_schedules = {
            symbol: (
                ZoneInfo(schedule.timezone),
                tuple((_minutes(window.split("-", 1)[0]), _minutes(window.split("-", 1)[1])) for window in schedule.windows),
            )
            for symbol, schedule in config.entry_schedules.items()
        }

    def evaluate(
        self,
        group: str,
        now: datetime | None = None,
        *,
        canonical_symbol: str | None = None,
    ) -> tuple[bool, str]:
        if not self.config.weekend_guard_enabled or group not in self.config.guarded_groups:
            return self._intraday_window(canonical_symbol, now)

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
        if blocked:
            return (
                False,
                "weekend entry guard: new positions are blocked from "
                f"Friday {self.config.friday_entry_cutoff_utc} UTC until "
                f"Sunday {self.config.sunday_entry_resume_utc} UTC",
            )
        return self._intraday_window(canonical_symbol, current)

    def _intraday_window(
        self,
        canonical_symbol: str | None,
        now: datetime | None,
    ) -> tuple[bool, str]:
        if canonical_symbol is None or canonical_symbol not in self._entry_schedules:
            return True, "entry window open"
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("session guard requires a timezone-aware datetime")
        timezone_info, windows = self._entry_schedules[canonical_symbol]
        local = current.astimezone(timezone_info)
        minute_of_day = local.hour * 60 + local.minute
        for start, end in windows:
            inside = start <= minute_of_day < end if start < end else minute_of_day >= start or minute_of_day < end
            if inside:
                return True, f"configured entry window open for {canonical_symbol}"
        windows_text = ", ".join(
            f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}" for start, end in windows
        )
        return (
            False,
            f"outside configured entry window for {canonical_symbol}: {windows_text} {timezone_info.key}",
        )

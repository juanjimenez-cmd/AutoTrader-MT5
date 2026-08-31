"""Freshness checks that prevent entries from stale ticks or closed candles."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import MarketDataConfig
from .models import Candle


TIMEFRAME_SECONDS = {"M5": 300, "M15": 900}


class MarketDataGuard:
    def __init__(self, config: MarketDataConfig):
        self.config = config

    def evaluate(
        self,
        candles_by_timeframe: dict[str, list[Candle]],
        latest_tick_time: int,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("market-data guard requires a timezone-aware datetime")
        current_timestamp = current.astimezone(timezone.utc).timestamp()

        tick_age = current_timestamp - latest_tick_time
        if tick_age < -self.config.future_tolerance_seconds:
            return False, f"latest tick is {-tick_age:.0f}s in the future"
        if tick_age > self.config.max_tick_age_seconds:
            return False, (
                f"latest tick is stale by {tick_age:.0f}s "
                f"(maximum {self.config.max_tick_age_seconds}s)"
            )

        for timeframe, candles in candles_by_timeframe.items():
            if not candles:
                return False, f"no {timeframe} candles available"
            timeframe_seconds = TIMEFRAME_SECONDS.get(timeframe)
            if timeframe_seconds is None:
                return False, f"unsupported timeframe {timeframe}"
            bar_age = current_timestamp - candles[-1].time
            maximum_age = 2 * timeframe_seconds + self.config.closed_bar_grace_seconds
            if bar_age < -self.config.future_tolerance_seconds:
                return False, f"latest {timeframe} candle is {-bar_age:.0f}s in the future"
            if bar_age > maximum_age:
                return False, (
                    f"latest {timeframe} candle is stale by {bar_age:.0f}s "
                    f"(maximum {maximum_age}s)"
                )
        return True, "market data is fresh"

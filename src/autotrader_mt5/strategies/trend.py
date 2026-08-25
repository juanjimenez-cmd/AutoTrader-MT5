from __future__ import annotations

from .base import Strategy
from ..indicators import atr, clamp, ema_series
from ..models import Candle, Direction, SignalVote


class TrendStrategy(Strategy):
    name = "trend"
    weight = 1.15

    def evaluate(self, candles: list[Candle], timeframe: str) -> SignalVote:
        closes = [candle.close for candle in candles]
        if len(closes) < 35:
            return SignalVote(self.name, timeframe, Direction.FLAT, 0.0, "insufficient candles", self.weight)
        fast = ema_series(closes, 12)
        slow = ema_series(closes, 26)
        volatility = max(atr(candles), 1e-12)
        gap = (fast[-1] - slow[-1]) / volatility
        slope = (slow[-1] - slow[-4]) / volatility
        if gap > 0 and slope > 0:
            direction = Direction.LONG
        elif gap < 0 and slope < 0:
            direction = Direction.SHORT
        else:
            direction = Direction.FLAT
        strength = clamp((abs(gap) + abs(slope)) / 1.5) if direction is not Direction.FLAT else 0.0
        return SignalVote(self.name, timeframe, direction, strength, f"EMA gap={gap:.2f}, slope={slope:.2f}", self.weight)

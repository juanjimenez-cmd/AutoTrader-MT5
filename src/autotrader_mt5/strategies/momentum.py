from __future__ import annotations

from .base import Strategy
from ..indicators import clamp, rsi
from ..models import Candle, Direction, SignalVote


class MomentumStrategy(Strategy):
    name = "momentum"
    weight = 1.0

    def evaluate(self, candles: list[Candle], timeframe: str) -> SignalVote:
        closes = [candle.close for candle in candles]
        if len(closes) < 20:
            return SignalVote(self.name, timeframe, Direction.FLAT, 0.0, "insufficient candles", self.weight)
        value = rsi(closes)
        rate = closes[-1] / closes[-6] - 1
        if value >= 55 and rate > 0:
            direction = Direction.LONG
            strength = clamp((value - 50) / 25 + min(abs(rate) * 20, 0.25))
        elif value <= 45 and rate < 0:
            direction = Direction.SHORT
            strength = clamp((50 - value) / 25 + min(abs(rate) * 20, 0.25))
        else:
            direction = Direction.FLAT
            strength = 0.0
        return SignalVote(self.name, timeframe, direction, strength, f"RSI={value:.1f}, ROC5={rate:.3%}", self.weight)

from __future__ import annotations

from .base import Strategy
from ..indicators import clamp, sma, standard_deviation
from ..models import Candle, Direction, SignalVote


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    weight = 0.8

    def evaluate(self, candles: list[Candle], timeframe: str) -> SignalVote:
        closes = [candle.close for candle in candles]
        if len(closes) < 25:
            return SignalVote(self.name, timeframe, Direction.FLAT, 0.0, "insufficient candles", self.weight)
        middle = sma(closes, 20)
        deviation = standard_deviation(closes, 20)
        if deviation <= 0:
            return SignalVote(self.name, timeframe, Direction.FLAT, 0.0, "zero volatility", self.weight)
        z_score = (closes[-1] - middle) / deviation
        if z_score <= -2.0:
            direction = Direction.LONG
            strength = clamp((abs(z_score) - 1.5) / 1.5)
        elif z_score >= 2.0:
            direction = Direction.SHORT
            strength = clamp((abs(z_score) - 1.5) / 1.5)
        else:
            direction = Direction.FLAT
            strength = 0.0
        return SignalVote(self.name, timeframe, direction, strength, f"20-bar z-score={z_score:.2f}", self.weight)

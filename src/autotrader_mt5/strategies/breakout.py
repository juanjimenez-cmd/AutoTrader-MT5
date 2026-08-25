from __future__ import annotations

from .base import Strategy
from ..indicators import atr, clamp
from ..models import Candle, Direction, SignalVote


class BreakoutStrategy(Strategy):
    name = "breakout"
    weight = 1.1

    def evaluate(self, candles: list[Candle], timeframe: str) -> SignalVote:
        if len(candles) < 25:
            return SignalVote(self.name, timeframe, Direction.FLAT, 0.0, "insufficient candles", self.weight)
        current = candles[-1]
        window = candles[-21:-1]
        upper = max(candle.high for candle in window)
        lower = min(candle.low for candle in window)
        volatility = max(atr(candles), 1e-12)
        if current.close > upper:
            distance = (current.close - upper) / volatility
            return SignalVote(self.name, timeframe, Direction.LONG, clamp(0.55 + distance), f"close above 20-bar high by {distance:.2f} ATR", self.weight)
        if current.close < lower:
            distance = (lower - current.close) / volatility
            return SignalVote(self.name, timeframe, Direction.SHORT, clamp(0.55 + distance), f"close below 20-bar low by {distance:.2f} ATR", self.weight)
        return SignalVote(self.name, timeframe, Direction.FLAT, 0.0, "inside 20-bar range", self.weight)

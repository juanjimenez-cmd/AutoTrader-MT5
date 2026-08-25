from __future__ import annotations

from abc import ABC, abstractmethod
from ..models import Candle, SignalVote


class Strategy(ABC):
    name: str
    weight: float = 1.0

    @abstractmethod
    def evaluate(self, candles: list[Candle], timeframe: str) -> SignalVote:
        """Return a normalized vote without placing an order."""

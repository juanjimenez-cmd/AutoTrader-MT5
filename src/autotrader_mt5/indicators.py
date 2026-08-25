"""Small, deterministic technical indicators used by every runtime."""

from __future__ import annotations

from math import sqrt
from .models import Candle


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need {period} values")
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError(f"need more than {period} values")
    changes = [b - a for a, b in zip(values[-period - 1 : -1], values[-period:])]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0
    relative_strength = gains / losses
    return 100.0 - (100.0 / (1.0 + relative_strength))


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) <= period:
        raise ValueError(f"need more than {period} candles")
    true_ranges: list[float] = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:]):
        true_ranges.append(
            max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))
        )
    return sum(true_ranges) / period


def standard_deviation(values: list[float], period: int) -> float:
    sample = values[-period:]
    mean = sum(sample) / len(sample)
    return sqrt(sum((value - mean) ** 2 for value in sample) / len(sample))


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))

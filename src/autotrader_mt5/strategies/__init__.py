"""Initial modular strategy set."""

from .base import Strategy
from .breakout import BreakoutStrategy
from .mean_reversion import MeanReversionStrategy
from .momentum import MomentumStrategy
from .trend import TrendStrategy

DEFAULT_STRATEGIES: tuple[Strategy, ...] = (
    TrendStrategy(),
    BreakoutStrategy(),
    MomentumStrategy(),
    MeanReversionStrategy(),
)

__all__ = [
    "Strategy",
    "TrendStrategy",
    "BreakoutStrategy",
    "MomentumStrategy",
    "MeanReversionStrategy",
    "DEFAULT_STRATEGIES",
]

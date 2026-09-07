"""Components of the conservative trend-breakout strategy."""

from .base import Strategy
from .breakout import BreakoutStrategy
from .momentum import MomentumStrategy
from .trend import TrendStrategy

DEFAULT_STRATEGIES: tuple[Strategy, ...] = (
    TrendStrategy(),
    BreakoutStrategy(),
    MomentumStrategy(),
)

__all__ = [
    "Strategy",
    "TrendStrategy",
    "BreakoutStrategy",
    "MomentumStrategy",
    "DEFAULT_STRATEGIES",
]

"""AutoTrader-MT5: demo-only multi-asset trading engine."""

from .config import AppConfig, load_config
from .engine import AutoTrader
from .models import Direction, ScoredSignal

__all__ = ["AppConfig", "AutoTrader", "Direction", "ScoredSignal", "load_config"]
__version__ = "1.0.0"

"""Dependency-free domain models shared by live trading and backtesting."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"

    @property
    def sign(self) -> int:
        return {Direction.LONG: 1, Direction.SHORT: -1, Direction.FLAT: 0}[self]


@dataclass(frozen=True, slots=True)
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True, slots=True)
class SignalVote:
    strategy: str
    timeframe: str
    direction: Direction
    strength: float
    reason: str
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class ScoredSignal:
    canonical_symbol: str
    broker_symbol: str
    direction: Direction
    score: int
    entry: float
    stop_loss: float
    take_profit: float
    atr: float
    votes: tuple[SignalVote, ...]
    timestamp: int


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    balance: float
    equity: float
    server: str
    trade_mode: int
    currency: str = "USD"
    day_start_balance: float | None = None
    daily_pnl: float | None = None


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    name: str
    digits: int
    point: float
    volume_min: float
    volume_max: float
    volume_step: float
    filling_mode: int = 0


@dataclass(frozen=True, slots=True)
class Position:
    ticket: int
    symbol: str
    canonical_symbol: str
    direction: Direction
    volume: float
    entry: float
    current_price: float
    stop_loss: float
    take_profit: float
    group: str
    risk_percent: float = 0.0
    initial_stop_loss: float | None = None
    magic: int = 0


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    canonical_symbol: str
    direction: Direction
    volume: float
    price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    score: int
    comment: str = "AutoTrader-v1"


@dataclass(frozen=True, slots=True)
class OrderResult:
    accepted: bool
    ticket: int = 0
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reason: str
    risk_percent: float = 0.0
    risk_amount: float = 0.0

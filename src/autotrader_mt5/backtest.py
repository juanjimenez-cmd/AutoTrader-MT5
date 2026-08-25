"""Conservative single-symbol backtester using the live SignalEngine and RiskManager."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from .config import AppConfig
from .models import AccountSnapshot, Candle, Direction, Position
from .risk import RiskManager
from .signals import SignalEngine


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    direction: str
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    stop_loss: float
    take_profit: float
    score: int
    pnl: float
    r_multiple: float
    exit_reason: str


@dataclass(frozen=True, slots=True)
class BacktestReport:
    symbol: str
    initial_equity: float
    final_equity: float
    return_percent: float
    max_drawdown_percent: float
    trades: tuple[BacktestTrade, ...]

    @property
    def wins(self) -> int:
        return sum(trade.pnl > 0 for trade in self.trades)

    @property
    def win_rate(self) -> float:
        return self.wins / len(self.trades) * 100 if self.trades else 0.0

    def to_json(self) -> str:
        body = asdict(self)
        body["wins"] = self.wins
        body["win_rate"] = self.win_rate
        return json.dumps(body, indent=2, sort_keys=True)


def _parse_time(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())


def load_candles_csv(path: str | Path) -> list[Candle]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        required = {"time", "open", "high", "low", "close"}
        if not required.issubset(rows.fieldnames or ()):
            raise ValueError(f"CSV needs columns {sorted(required)}")
        candles = [
            Candle(
                time=_parse_time(row["time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or row.get("tick_volume") or 0.0),
            )
            for row in rows
        ]
    candles.sort(key=lambda item: item.time)
    return candles


def aggregate(candles: list[Candle], seconds: int = 900) -> list[Candle]:
    result: list[Candle] = []
    bucket: list[Candle] = []
    bucket_id: int | None = None
    for candle in candles:
        current_id = candle.time // seconds
        if bucket and current_id != bucket_id:
            result.append(
                Candle(
                    time=bucket[0].time,
                    open=bucket[0].open,
                    high=max(item.high for item in bucket),
                    low=min(item.low for item in bucket),
                    close=bucket[-1].close,
                    volume=sum(item.volume for item in bucket),
                )
            )
            bucket = []
        bucket_id = current_id
        bucket.append(candle)
    if bucket:
        result.append(
            Candle(
                time=bucket[0].time,
                open=bucket[0].open,
                high=max(item.high for item in bucket),
                low=min(item.low for item in bucket),
                close=bucket[-1].close,
                volume=sum(item.volume for item in bucket),
            )
        )
    return result


class Backtester:
    def __init__(self, config: AppConfig, initial_equity: float = 10_000.0):
        self.config = config
        self.initial_equity = initial_equity
        self.signal_engine = SignalEngine()
        self.risk_manager = RiskManager(config)

    def run(self, canonical_symbol: str, m5_candles: list[Candle]) -> BacktestReport:
        if len(m5_candles) < self.config.candle_count + 10:
            raise ValueError("Not enough M5 candles for configured candle_count")
        m15_candles = aggregate(m5_candles)
        profile = self.config.profile_for(canonical_symbol)
        equity = peak = self.initial_equity
        max_drawdown = 0.0
        active: dict | None = None
        trades: list[BacktestTrade] = []

        for index in range(self.config.candle_count, len(m5_candles)):
            current = m5_candles[index]
            if active is not None:
                stop_hit = current.low <= active["stop"] if active["direction"] is Direction.LONG else current.high >= active["stop"]
                target_hit = current.high >= active["target"] if active["direction"] is Direction.LONG else current.low <= active["target"]
                if stop_hit or target_hit:
                    # If both occur inside one candle, assume the stop was first.
                    exit_price = active["stop"] if stop_hit else active["target"]
                    reason = "stop_loss" if stop_hit else "take_profit"
                    r_multiple = -1.0 if stop_hit else profile.reward_risk
                    pnl = active["risk_amount"] * r_multiple
                    equity += pnl
                    trades.append(
                        BacktestTrade(
                            direction=active["direction"].value,
                            entry_time=active["time"],
                            exit_time=current.time,
                            entry=active["entry"],
                            exit=exit_price,
                            stop_loss=active["stop"],
                            take_profit=active["target"],
                            score=active["score"],
                            pnl=pnl,
                            r_multiple=r_multiple,
                            exit_reason=reason,
                        )
                    )
                    active = None
                    peak = max(peak, equity)
                    max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
            if active is not None:
                continue

            m5_window = m5_candles[index - self.config.candle_count : index]
            # A derived M15 bar is usable only after all three M5 bars have closed.
            m15_available = [item for item in m15_candles if item.time + 900 <= current.time]
            if len(m15_available) < 35:
                continue
            signal = self.signal_engine.evaluate(
                canonical_symbol,
                canonical_symbol,
                {"M5": m5_window, "M15": m15_available[-self.config.candle_count :]},
                profile.atr_stop_multiplier,
                profile.reward_risk,
            )
            account = AccountSnapshot(equity, equity, "BACKTEST-DEMO", 0)
            now = datetime.fromtimestamp(current.time, timezone.utc)
            decision = self.risk_manager.evaluate(signal, account, (), now=now)
            if decision.allowed:
                active = {
                    "direction": signal.direction,
                    "time": current.time,
                    "entry": signal.entry,
                    "stop": signal.stop_loss,
                    "target": signal.take_profit,
                    "score": signal.score,
                    "risk_amount": decision.risk_amount,
                }

        if active is not None:
            final = m5_candles[-1]
            distance = (final.close - active["entry"]) * active["direction"].sign
            initial_risk = abs(active["entry"] - active["stop"])
            r_multiple = distance / initial_risk if initial_risk else 0.0
            pnl = active["risk_amount"] * r_multiple
            equity += pnl
            trades.append(
                BacktestTrade(
                    direction=active["direction"].value,
                    entry_time=active["time"],
                    exit_time=final.time,
                    entry=active["entry"],
                    exit=final.close,
                    stop_loss=active["stop"],
                    take_profit=active["target"],
                    score=active["score"],
                    pnl=pnl,
                    r_multiple=r_multiple,
                    exit_reason="end_of_data",
                )
            )
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        return BacktestReport(
            symbol=canonical_symbol,
            initial_equity=self.initial_equity,
            final_equity=equity,
            return_percent=(equity / self.initial_equity - 1) * 100,
            max_drawdown_percent=max_drawdown,
            trades=tuple(trades),
        )

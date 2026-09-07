"""Conservative single-symbol backtester using the live SignalEngine and RiskManager."""

from __future__ import annotations

import csv
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from .config import AppConfig
from .models import AccountSnapshot, Candle, Direction, Position
from .risk import RiskManager
from .sessions import EntrySessionGuard
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
    gross_pnl: float
    friction_cost: float
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

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(trade.pnl for trade in self.trades if trade.pnl > 0)
        gross_loss = abs(sum(trade.pnl for trade in self.trades if trade.pnl < 0))
        return gross_profit / gross_loss if gross_loss else 0.0

    @property
    def gross_pnl(self) -> float:
        return sum(trade.gross_pnl for trade in self.trades)

    @property
    def friction_cost(self) -> float:
        return sum(trade.friction_cost for trade in self.trades)

    def to_dict(self) -> dict:
        body = asdict(self)
        body["wins"] = self.wins
        body["win_rate"] = self.win_rate
        body["profit_factor"] = self.profit_factor
        body["gross_pnl"] = self.gross_pnl
        body["friction_cost"] = self.friction_cost
        return body

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    """Chronological validation; the forward portion is never used for tuning."""

    symbol: str
    forward_start: int
    in_sample: BacktestReport
    forward: BacktestReport

    def to_json(self) -> str:
        return json.dumps(
            {
                "symbol": self.symbol,
                "forward_start": datetime.fromtimestamp(self.forward_start, timezone.utc).isoformat(),
                "in_sample": self.in_sample.to_dict(),
                "forward": self.forward.to_dict(),
            },
            indent=2,
            sort_keys=True,
        )


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
        self.session_guard = EntrySessionGuard(config.sessions)

    def _friction_cost(self, risk_amount: float, entry: float, stop_loss: float) -> float:
        """Estimated round-trip cost in account currency for a quote-currency USD pair.

        The default contract values model EURUSD. Other instruments need their
        own values in the TOML before their reports can be interpreted.
        """
        costs = self.config.backtest_costs
        stop_distance = abs(entry - stop_loss)
        if stop_distance == 0:
            return 0.0
        volume_lots = risk_amount / (costs.contract_size * stop_distance)
        execution_cost = (
            costs.spread_pips + 2 * costs.slippage_pips_per_side
        ) * costs.pip_size * costs.contract_size * volume_lots
        commission_cost = costs.commission_per_lot_round_turn * volume_lots
        return execution_cost + commission_cost

    def run(
        self,
        canonical_symbol: str,
        m5_candles: list[Candle],
        *,
        entry_start_time: int | None = None,
    ) -> BacktestReport:
        if len(m5_candles) < self.config.candle_count + 10:
            raise ValueError("Not enough M5 candles for configured candle_count")
        m15_candles = aggregate(m5_candles)
        h1_candles = aggregate(m5_candles, seconds=3600)
        profile = self.config.profile_for(canonical_symbol)
        equity = peak = self.initial_equity
        max_drawdown = 0.0
        active: dict | None = None
        trades: list[BacktestTrade] = []
        last_entry_time: int | None = None
        entries_by_utc_date: dict[object, int] = {}
        m15_close_times = [item.time + 900 for item in m15_candles]
        h1_close_times = [item.time + 3600 for item in h1_candles]

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
                    gross_pnl = active["risk_amount"] * r_multiple
                    friction_cost = self._friction_cost(
                        active["risk_amount"], active["entry"], active["stop"]
                    )
                    pnl = gross_pnl - friction_cost
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
                            gross_pnl=gross_pnl,
                            friction_cost=friction_cost,
                            pnl=pnl,
                            r_multiple=pnl / active["risk_amount"],
                            exit_reason=reason,
                        )
                    )
                    active = None
                    peak = max(peak, equity)
                    max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
            if active is not None:
                continue
            if entry_start_time is not None and current.time < entry_start_time:
                continue

            m5_window = m5_candles[index - self.config.candle_count : index]
            # A derived M15 bar is usable only after all three M5 bars have closed.
            m15_end = bisect_right(m15_close_times, current.time)
            h1_end = bisect_right(h1_close_times, current.time)
            if m15_end < 35 or h1_end < 35:
                continue
            signal = self.signal_engine.evaluate(
                canonical_symbol,
                canonical_symbol,
                {
                    "M5": m5_window,
                    "M15": m15_candles[max(0, m15_end - self.config.candle_count) : m15_end],
                    "H1": h1_candles[max(0, h1_end - self.config.candle_count) : h1_end],
                },
                profile.atr_stop_multiplier,
                profile.reward_risk,
            )
            account = AccountSnapshot(equity, equity, "BACKTEST-DEMO", 0)
            now = datetime.fromtimestamp(current.time, timezone.utc)
            entry_allowed, _ = self.session_guard.evaluate(
                profile.group, now, canonical_symbol=canonical_symbol
            )
            if not entry_allowed:
                continue
            if last_entry_time is not None and (
                current.time - last_entry_time < self.config.entry_controls.cooldown_minutes * 60
            ):
                continue
            utc_day = now.date()
            if entries_by_utc_date.get(utc_day, 0) >= self.config.entry_controls.max_entries_per_symbol_day:
                continue
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
                last_entry_time = current.time
                entries_by_utc_date[utc_day] = entries_by_utc_date.get(utc_day, 0) + 1

        if active is not None:
            final = m5_candles[-1]
            distance = (final.close - active["entry"]) * active["direction"].sign
            initial_risk = abs(active["entry"] - active["stop"])
            r_multiple = distance / initial_risk if initial_risk else 0.0
            gross_pnl = active["risk_amount"] * r_multiple
            friction_cost = self._friction_cost(
                active["risk_amount"], active["entry"], active["stop"]
            )
            pnl = gross_pnl - friction_cost
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
                    gross_pnl=gross_pnl,
                    friction_cost=friction_cost,
                    pnl=pnl,
                    r_multiple=pnl / active["risk_amount"],
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

    def run_walk_forward(
        self, canonical_symbol: str, m5_candles: list[Candle], forward_start: int
    ) -> WalkForwardReport:
        split_at = bisect_right([candle.time for candle in m5_candles], forward_start - 1)
        in_sample_candles = m5_candles[:split_at]
        if len(in_sample_candles) < self.config.candle_count + 10:
            raise ValueError("Not enough candles before forward_start for the in-sample test")
        if len(m5_candles) - split_at < self.config.candle_count + 10:
            raise ValueError("Not enough candles after forward_start for the forward test")
        return WalkForwardReport(
            symbol=canonical_symbol,
            forward_start=forward_start,
            in_sample=self.run(canonical_symbol, in_sample_candles),
            forward=self.run(canonical_symbol, m5_candles, entry_start_time=forward_start),
        )

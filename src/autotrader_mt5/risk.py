"""Portfolio-aware risk gates. Every order must pass all gates."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import AppConfig
from .models import AccountSnapshot, Direction, Position, RiskDecision, ScoredSignal


class RiskManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self._day: str | None = None
        self._day_start_equity: float | None = None

    def reset_day_if_needed(self, account: AccountSnapshot, now: datetime | None = None) -> None:
        day = (now or datetime.now(timezone.utc)).date().isoformat()
        if day != self._day:
            self._day = day
            self._day_start_equity = account.equity

    def evaluate(
        self,
        signal: ScoredSignal,
        account: AccountSnapshot,
        positions: tuple[Position, ...],
        now: datetime | None = None,
    ) -> RiskDecision:
        self.reset_day_if_needed(account, now)
        if account.trade_mode != 0:
            return RiskDecision(False, "account is not MetaTrader DEMO")
        if signal.direction is Direction.FLAT:
            return RiskDecision(False, "signal is flat")
        if signal.score < self.config.min_score:
            return RiskDecision(False, f"score {signal.score} below minimum {self.config.min_score}")
        if signal.stop_loss <= 0 or signal.take_profit <= 0:
            return RiskDecision(False, "SL and TP are mandatory")
        if signal.direction is Direction.LONG and not signal.stop_loss < signal.entry < signal.take_profit:
            return RiskDecision(False, "invalid LONG stop geometry")
        if signal.direction is Direction.SHORT and not signal.take_profit < signal.entry < signal.stop_loss:
            return RiskDecision(False, "invalid SHORT stop geometry")

        if account.daily_pnl is not None and account.day_start_balance is not None:
            daily_loss = max(0.0, -account.daily_pnl) / max(account.day_start_balance, 1e-12) * 100
        else:
            start_equity = self._day_start_equity or account.equity
            daily_loss = max(0.0, start_equity - account.equity) / max(start_equity, 1e-12) * 100
        if daily_loss >= self.config.risk.daily_loss_limit_percent:
            return RiskDecision(False, f"daily loss limit reached ({daily_loss:.2f}%)")
        if len(positions) >= self.config.risk.max_positions:
            return RiskDecision(False, "maximum open positions reached")
        if any(position.canonical_symbol == signal.canonical_symbol for position in positions):
            return RiskDecision(False, "position already open for symbol")

        profile = self.config.profile_for(signal.canonical_symbol)
        requested = profile.risk_percent
        simultaneous = sum(max(0.0, position.risk_percent) for position in positions)
        if simultaneous + requested > self.config.risk.max_simultaneous_risk_percent + 1e-9:
            return RiskDecision(False, "maximum simultaneous risk exceeded")
        group_risk = sum(position.risk_percent for position in positions if position.group == profile.group)
        group_limit = self.config.risk.max_group_risk_percent.get(
            profile.group, self.config.risk.max_simultaneous_risk_percent
        )
        if group_risk + requested > group_limit + 1e-9:
            return RiskDecision(False, f"{profile.group} correlation-group limit exceeded")
        return RiskDecision(True, "approved", requested, account.equity * requested / 100.0)

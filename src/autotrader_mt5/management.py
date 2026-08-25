"""Breakeven and ATR trailing-stop management."""

from __future__ import annotations

from .broker import Broker
from .config import AppConfig
from .indicators import atr
from .models import Direction
from .storage import EventStore


class PositionManager:
    def __init__(self, config: AppConfig, broker: Broker, store: EventStore):
        self.config = config
        self.broker = broker
        self.store = store
        self._initial_stops: dict[int, float] = {}

    async def manage(self) -> None:
        positions = await self.broker.positions()
        managed_positions = tuple(
            position for position in positions if position.magic == self.config.magic_number
        )
        active_tickets = {position.ticket for position in managed_positions}
        self._initial_stops = {
            ticket: stop for ticket, stop in self._initial_stops.items() if ticket in active_tickets
        }
        for position in managed_positions:
            initial_stop = self._initial_stops.setdefault(
                position.ticket, position.initial_stop_loss or position.stop_loss
            )
            initial_risk = abs(position.entry - initial_stop)
            if initial_risk <= 0:
                continue
            profit_distance = (position.current_price - position.entry) * position.direction.sign
            r_multiple = profit_distance / initial_risk
            new_stop = position.stop_loss
            if r_multiple >= self.config.management.breakeven_at_r:
                if position.direction is Direction.LONG:
                    new_stop = max(new_stop, position.entry)
                else:
                    new_stop = min(new_stop or float("inf"), position.entry)
            if r_multiple >= self.config.management.trailing_start_at_r:
                candles = await self.broker.candles(position.symbol, "M5", 30)
                trail = atr(candles) * self.config.management.trailing_atr_multiplier
                candidate = position.current_price - trail * position.direction.sign
                if position.direction is Direction.LONG:
                    new_stop = max(new_stop, candidate)
                else:
                    new_stop = min(new_stop, candidate)
            improved = (
                new_stop > position.stop_loss + 1e-12
                if position.direction is Direction.LONG
                else new_stop < position.stop_loss - 1e-12
            )
            if improved:
                result = await self.broker.update_stops(
                    position.ticket, position.symbol, new_stop, position.take_profit
                )
                self.store.record(
                    "position_management",
                    {"ticket": position.ticket, "r_multiple": r_multiple, "new_stop": new_stop, "result": result},
                    position.canonical_symbol,
                )

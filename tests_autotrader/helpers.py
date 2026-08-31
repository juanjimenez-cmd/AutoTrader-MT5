from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from autotrader_mt5.config import AppConfig, load_config
from autotrader_mt5.models import AccountSnapshot, Candle, Direction, OrderRequest, OrderResult, Position, SymbolSpec


ROOT = Path(__file__).resolve().parents[1]
FAKE_NOW = int(datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc).timestamp())


def test_config(**changes) -> AppConfig:
    return replace(load_config(ROOT / "configs" / "autotrader.toml"), log_directory=ROOT / "work-test-logs", **changes)


def rising_candles(count: int = 140, start: float = 100.0, end_time: int | None = None) -> list[Candle]:
    result = []
    price = start
    first_time = 0 if end_time is None else end_time - count * 300
    for index in range(count):
        change = 0.15 + index * 0.001
        opened = price
        price += change
        result.append(Candle(first_time + index * 300, opened, price + 0.05, opened - 0.03, price, 100 + index))
    return result


class FakeBroker:
    def __init__(self):
        self.connected = False
        self.mapping = {}
        self.orders: list[OrderRequest] = []
        self._positions: tuple[Position, ...] = ()
        self.disabled_symbols: set[str] = set()
        self.submit_rejection: str | None = None
        self.tick_time_value = FAKE_NOW
        self.candle_lag_seconds = 0

    async def connect(self): self.connected = True
    async def close(self): self.connected = False
    def set_symbol_mapping(self, mapping): self.mapping = mapping
    async def available_symbols(self): return ("EURUSD.a", "GBPUSD.a", "USDJPY.a", "GOLD.a", "USTEC.a", "US500.a", "BTCUSD.a", "ETHUSD.a")
    async def account(self): return AccountSnapshot(10_000, 10_000, "Broker-Demo", 0)
    async def positions(self): return self._positions
    async def latest_tick_time(self, symbol): return self.tick_time_value
    async def candles(self, symbol, timeframe, count):
        data = rising_candles(
            max(count, 140),
            end_time=self.tick_time_value - self.candle_lag_seconds,
        )
        return data[-count:]
    async def symbol_spec(self, symbol): return SymbolSpec(symbol, 2, 0.01, 0.01, 100, 0.01)
    async def validate_symbol(self, symbol, direction=None):
        return (False, "trade disabled") if symbol in self.disabled_symbols else (True, "tradable")
    async def prepare_order_levels(self, symbol, direction, stop_loss, take_profit, reward_risk):
        entry = (take_profit + reward_risk * stop_loss) / (reward_risk + 1)
        return entry, stop_loss, take_profit
    async def volume_for_risk(self, symbol, direction, entry, stop_loss, risk_amount): return 0.10
    async def margin_required(self, symbol, direction, volume, price): return 100.0
    async def submit(self, request):
        self.orders.append(request)
        if self.submit_rejection:
            return OrderResult(False, message=self.submit_rejection)
        return OrderResult(True, 1000 + len(self.orders), "accepted")
    async def update_stops(self, ticket, symbol, stop_loss, take_profit): return OrderResult(True, ticket, "updated")

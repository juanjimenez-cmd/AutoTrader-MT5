"""Async broker adapter for native Windows MT5 and the local macOS bridge."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import AppConfig
from .models import AccountSnapshot, Candle, Direction, OrderRequest, OrderResult, Position, SymbolSpec
from .mt5_runtime import MT5Runtime


class DemoSafetyError(RuntimeError):
    pass


def _field(item: Any, name: str, default: Any = None) -> Any:
    """Read named tuples, remote objects, mappings, and NumPy structured rows."""
    if isinstance(item, Mapping):
        return item.get(name, default)
    try:
        return item[name]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return getattr(item, name, default)


def _round_down(value: float, increment: float, digits: int) -> float:
    return round(math.floor((value + 1e-12) / increment) * increment, digits)


def _round_up(value: float, increment: float, digits: int) -> float:
    return round(math.ceil((value - 1e-12) / increment) * increment, digits)


class MT5Broker:
    def __init__(self, config: AppConfig, runtime: MT5Runtime | None = None):
        self.config = config
        self.runtime = runtime or MT5Runtime(
            config.mt5.backend,
            bridge_host=config.mt5.bridge_host,
            bridge_port=config.mt5.bridge_port,
        )
        self._canonical_by_broker: dict[str, str] = {}

    async def _call(self, name: str, *args: object, **kwargs: object) -> Any:
        return await asyncio.to_thread(self.runtime.call, name, *args, **kwargs)

    async def connect(self) -> None:
        credentials_path = Path(self.config.credentials_file)
        if not credentials_path.exists():
            raise FileNotFoundError(
                f"Missing {credentials_path}. Copy configs/autotrader.credentials.example.json "
                "and fill DEMO credentials."
            )
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
        if not isinstance(credentials, dict):
            raise ValueError("The credentials file must contain a JSON object")
        try:
            await asyncio.to_thread(self.runtime.connect, credentials)
            account = await self._call("account_info")
            demo_mode = self.runtime.constant("ACCOUNT_TRADE_MODE_DEMO", 0)
            if account is None or int(_field(account, "trade_mode", -1)) != demo_mode:
                raise DemoSafetyError("Hard stop: AutoTrader-MT5 v1 accepts DEMO accounts only")
        except Exception:
            await asyncio.to_thread(self.runtime.close)
            raise

    async def close(self) -> None:
        await asyncio.to_thread(self.runtime.close)

    def set_symbol_mapping(self, mapping: dict[str, str]) -> None:
        self._canonical_by_broker = {broker: canonical for canonical, broker in mapping.items()}

    async def available_symbols(self) -> tuple[str, ...]:
        symbols = await self._call("symbols_get")
        return tuple(str(_field(item, "name", "")) for item in (symbols or ()) if _field(item, "name"))

    def _timestamp_to_utc(self, value: int) -> int:
        if self.runtime.backend != "bridge":
            return value
        try:
            server_timezone = ZoneInfo(self.config.market_data.bridge_server_timezone)
        except ZoneInfoNotFoundError as error:
            raise RuntimeError(
                "Unknown bridge server timezone "
                f"{self.config.market_data.bridge_server_timezone!r}"
            ) from error
        # The macOS bridge serializes MT5 server wall-clock fields as though they
        # were UTC. Reattach the configured server timezone, then convert to epoch UTC.
        server_wall_clock = datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=server_timezone)
        return int(server_wall_clock.timestamp())

    async def latest_tick_time(self, symbol: str) -> int:
        tick = await self._call("symbol_info_tick", symbol)
        raw_time = int(_field(tick, "time", 0)) if tick is not None else 0
        if raw_time <= 0:
            raise RuntimeError(f"No valid current tick time for {symbol}")
        return self._timestamp_to_utc(raw_time)

    async def candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        timeframe_value = self.runtime.constant(f"TIMEFRAME_{timeframe}")
        if not await self._call("symbol_select", symbol, True):
            raise RuntimeError(f"Broker refused to select {symbol} in Market Watch")
        rates = await self._call("copy_rates_from_pos", symbol, timeframe_value, 1, count)
        if rates is None or len(rates) < count:
            raise RuntimeError(f"Insufficient {timeframe} candles for {symbol}: {0 if rates is None else len(rates)}")
        return [
            Candle(
                time=self._timestamp_to_utc(int(_field(row, "time"))),
                open=float(_field(row, "open")),
                high=float(_field(row, "high")),
                low=float(_field(row, "low")),
                close=float(_field(row, "close")),
                volume=float(_field(row, "tick_volume", _field(row, "volume", 0.0))),
            )
            for row in rates
        ]

    async def account(self) -> AccountSnapshot:
        info = await self._call("account_info")
        if info is None:
            raise ConnectionError("MetaTrader account_info returned no data")
        demo_mode = self.runtime.constant("ACCOUNT_TRADE_MODE_DEMO", 0)
        trade_mode = int(_field(info, "trade_mode", -1))
        if trade_mode != demo_mode:
            raise DemoSafetyError("Hard stop: account changed and is no longer DEMO")
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        deals = await self._call("history_deals_get", start, now) or ()
        non_pnl_types = {
            self.runtime.constant("DEAL_TYPE_BALANCE"),
            self.runtime.constant("DEAL_TYPE_CREDIT"),
            self.runtime.constant("DEAL_TYPE_CORRECTION"),
            self.runtime.constant("DEAL_TYPE_BONUS"),
        }
        realized = sum(
            float(_field(deal, "profit", 0.0))
            + float(_field(deal, "commission", 0.0))
            + float(_field(deal, "swap", 0.0))
            + float(_field(deal, "fee", 0.0))
            for deal in deals
            if int(_field(deal, "type", -1)) not in non_pnl_types
        )
        balance = float(_field(info, "balance"))
        equity = float(_field(info, "equity"))
        return AccountSnapshot(
            balance=balance,
            equity=equity,
            server=str(_field(info, "server", "")),
            trade_mode=trade_mode,
            currency=str(_field(info, "currency", "")),
            day_start_balance=balance - realized,
            daily_pnl=realized + (equity - balance),
            margin=float(_field(info, "margin", 0.0)),
        )

    async def symbol_spec(self, symbol: str) -> SymbolSpec:
        info = await self._call("symbol_info", symbol)
        if info is None:
            raise RuntimeError(f"No symbol specification for {symbol}")
        return SymbolSpec(
            name=symbol,
            digits=int(_field(info, "digits")),
            point=float(_field(info, "point")),
            volume_min=float(_field(info, "volume_min")),
            volume_max=float(_field(info, "volume_max")),
            volume_step=float(_field(info, "volume_step")),
            filling_mode=int(_field(info, "filling_mode")),
            trade_mode=int(_field(info, "trade_mode", self.runtime.constant("SYMBOL_TRADE_MODE_FULL", 4))),
            stops_level=int(_field(info, "trade_stops_level", 0)),
            freeze_level=int(_field(info, "trade_freeze_level", 0)),
            tick_size=float(_field(info, "trade_tick_size", _field(info, "point", 0.0))),
        )

    async def validate_symbol(self, symbol: str, direction: Direction | None = None) -> tuple[bool, str]:
        spec = await self.symbol_spec(symbol)
        disabled = self.runtime.constant("SYMBOL_TRADE_MODE_DISABLED", 0)
        long_only = self.runtime.constant("SYMBOL_TRADE_MODE_LONGONLY", 1)
        short_only = self.runtime.constant("SYMBOL_TRADE_MODE_SHORTONLY", 2)
        close_only = self.runtime.constant("SYMBOL_TRADE_MODE_CLOSEONLY", 3)
        if spec.trade_mode in {disabled, close_only}:
            return False, f"{symbol} is not open for new trades (trade_mode={spec.trade_mode})"
        if direction is Direction.LONG and spec.trade_mode == short_only:
            return False, f"{symbol} allows short positions only"
        if direction is Direction.SHORT and spec.trade_mode == long_only:
            return False, f"{symbol} allows long positions only"
        if not await self._call("symbol_select", symbol, True):
            return False, f"broker refused to select {symbol} in Market Watch"
        return True, "tradable"

    async def prepare_order_levels(
        self, symbol: str, direction: Direction, stop_loss: float, take_profit: float, reward_risk: float
    ) -> tuple[float, float, float]:
        allowed, reason = await self.validate_symbol(symbol, direction)
        if not allowed:
            raise RuntimeError(reason)
        spec = await self.symbol_spec(symbol)
        tick = await self._call("symbol_info_tick", symbol)
        if tick is None:
            raise RuntimeError(f"No current tick for {symbol}")
        price = float(_field(tick, "ask") if direction is Direction.LONG else _field(tick, "bid"))
        increment = spec.tick_size if spec.tick_size > 0 else spec.point
        if increment <= 0 or spec.point <= 0:
            raise RuntimeError(f"Invalid price increment for {symbol}")
        if reward_risk <= 0:
            raise RuntimeError("reward/risk ratio must be positive")
        stale = (direction is Direction.LONG and not stop_loss < price < take_profit) or (
            direction is Direction.SHORT and not take_profit < price < stop_loss
        )
        if stale:
            raise RuntimeError(f"Signal levels for {symbol} are stale at current price {price}")
        # Two extra points protect against a small quote move between preparation and order_check.
        minimum_distance = max(spec.stops_level, 0) * spec.point + 2 * spec.point
        if direction is Direction.LONG:
            stop_loss = _round_down(min(stop_loss, price - minimum_distance), increment, spec.digits)
            risk_distance = price - stop_loss
            take_profit = _round_up(
                max(take_profit, price + minimum_distance, price + reward_risk * risk_distance),
                increment,
                spec.digits,
            )
        else:
            stop_loss = _round_up(max(stop_loss, price + minimum_distance), increment, spec.digits)
            risk_distance = stop_loss - price
            take_profit = _round_down(
                min(take_profit, price - minimum_distance, price - reward_risk * risk_distance),
                increment,
                spec.digits,
            )
        return round(price, spec.digits), stop_loss, take_profit

    async def positions(self) -> tuple[Position, ...]:
        raw_positions = await self._call("positions_get") or ()
        account = await self.account()
        buy_type = self.runtime.constant("POSITION_TYPE_BUY", 0)
        result: list[Position] = []
        for item in raw_positions:
            direction = Direction.LONG if int(_field(item, "type")) == buy_type else Direction.SHORT
            broker_symbol = str(_field(item, "symbol"))
            canonical = self._canonical_by_broker.get(broker_symbol, broker_symbol)
            profile = self.config.profile_for(canonical)
            price_open = float(_field(item, "price_open"))
            stop_loss = float(_field(item, "sl"))
            take_profit = float(_field(item, "tp"))
            initial_stop = stop_loss
            if take_profit > 0 and profile.reward_risk > 0:
                initial_distance = abs(take_profit - price_open) / profile.reward_risk
                initial_stop = price_open - initial_distance * direction.sign
            risk_percent = 0.0
            stop_is_risk = (direction is Direction.LONG and 0 < stop_loss < price_open) or (
                direction is Direction.SHORT and stop_loss > price_open
            )
            if stop_is_risk:
                loss = await self._call(
                    "order_calc_profit",
                    self._order_type(direction),
                    broker_symbol,
                    float(_field(item, "volume")),
                    price_open,
                    stop_loss,
                )
                risk_percent = abs(float(loss or 0.0)) / max(account.equity, 1e-12) * 100
            result.append(
                Position(
                    ticket=int(_field(item, "ticket")),
                    symbol=broker_symbol,
                    canonical_symbol=canonical,
                    direction=direction,
                    volume=float(_field(item, "volume")),
                    entry=price_open,
                    current_price=float(_field(item, "price_current")),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    group=profile.group,
                    risk_percent=risk_percent,
                    initial_stop_loss=initial_stop,
                    magic=int(_field(item, "magic", 0)),
                )
            )
        return tuple(result)

    def _order_type(self, direction: Direction) -> int:
        name = "ORDER_TYPE_BUY" if direction is Direction.LONG else "ORDER_TYPE_SELL"
        return self.runtime.constant(name)

    async def volume_for_risk(
        self, symbol: str, direction: Direction, entry: float, stop_loss: float, risk_amount: float
    ) -> float:
        loss_per_lot = await self._call("order_calc_profit", self._order_type(direction), symbol, 1.0, entry, stop_loss)
        if loss_per_lot is None or abs(float(loss_per_lot)) < 1e-12:
            raise RuntimeError(f"Cannot calculate stop-loss value for {symbol}")
        raw_volume = risk_amount / abs(float(loss_per_lot))
        spec = await self.symbol_spec(symbol)
        if raw_volume + 1e-12 < spec.volume_min:
            raise RuntimeError(
                f"Required volume {raw_volume:.6f} is below broker minimum {spec.volume_min}; refusing to exceed risk"
            )
        steps = math.floor((raw_volume + 1e-12) / spec.volume_step)
        volume = max(spec.volume_min, min(spec.volume_max, steps * spec.volume_step))
        precision = max(0, -int(math.floor(math.log10(spec.volume_step)))) if spec.volume_step < 1 else 0
        return round(volume, precision)

    async def margin_required(self, symbol: str, direction: Direction, volume: float, price: float) -> float:
        margin = await self._call("order_calc_margin", self._order_type(direction), symbol, volume, price)
        if margin is None or float(margin) < 0:
            raise RuntimeError(f"Cannot calculate required margin for {symbol}")
        return float(margin)

    def _filling(self, filling_mode: int) -> int:
        if filling_mode & 2:
            return self.runtime.constant("ORDER_FILLING_IOC")
        if filling_mode & 1:
            return self.runtime.constant("ORDER_FILLING_FOK")
        return self.runtime.constant("ORDER_FILLING_RETURN")

    async def submit(self, request: OrderRequest) -> OrderResult:
        await self.account()
        allowed, reason = await self.validate_symbol(request.symbol, request.direction)
        if not allowed:
            return OrderResult(False, message=f"preflight failed: {reason}")
        spec = await self.symbol_spec(request.symbol)
        order_type = self._order_type(request.direction)
        payload = {
            "action": self.runtime.constant("TRADE_ACTION_DEAL"),
            "symbol": request.symbol,
            "volume": request.volume,
            "type": order_type,
            "price": round(request.price, spec.digits),
            "sl": round(request.stop_loss, spec.digits),
            "tp": round(request.take_profit, spec.digits),
            "deviation": 20,
            "magic": self.config.magic_number,
            "comment": request.comment[:25],
            "type_time": self.runtime.constant("ORDER_TIME_GTC"),
            "type_filling": self._filling(spec.filling_mode),
        }
        checked = await self._call("order_check", dict(payload))
        if checked is None or int(_field(checked, "retcode", -1)) != 0:
            return OrderResult(False, message=f"order_check failed: {_field(checked, 'comment', 'no response')}")
        result = await self._call("order_send", payload)
        accepted_codes = {
            self.runtime.constant("TRADE_RETCODE_DONE"),
            self.runtime.constant("TRADE_RETCODE_DONE_PARTIAL"),
            self.runtime.constant("TRADE_RETCODE_PLACED"),
        }
        accepted = result is not None and int(_field(result, "retcode", -1)) in accepted_codes
        return OrderResult(
            accepted=accepted,
            ticket=int(_field(result, "order", 0) or _field(result, "deal", 0) or 0),
            message=str(_field(result, "comment", "no response")),
            raw={
                "retcode": int(_field(result, "retcode", -1)),
                "order": int(_field(result, "order", 0)),
                "deal": int(_field(result, "deal", 0)),
            },
        )

    async def update_stops(self, ticket: int, symbol: str, stop_loss: float, take_profit: float) -> OrderResult:
        await self.account()
        spec = await self.symbol_spec(symbol)
        result = await self._call(
            "order_send",
            {
                "action": self.runtime.constant("TRADE_ACTION_SLTP"),
                "position": ticket,
                "symbol": symbol,
                "sl": round(stop_loss, spec.digits),
                "tp": round(take_profit, spec.digits),
                "magic": self.config.magic_number,
            },
        )
        accepted = result is not None and int(_field(result, "retcode", -1)) == self.runtime.constant(
            "TRADE_RETCODE_DONE"
        )
        return OrderResult(accepted, ticket=ticket, message=str(_field(result, "comment", "no response")))

"""Live adapter for aiomql. Imports MT5 only when live mode is requested."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import AccountSnapshot, Candle, Direction, OrderRequest, OrderResult, Position, SymbolSpec


class DemoSafetyError(RuntimeError):
    pass


class MT5Broker:
    def __init__(self, config: AppConfig):
        self.config = config
        self.mt5: Any = None
        self._canonical_by_broker: dict[str, str] = {}

    def _imports(self) -> dict[str, Any]:
        try:
            from aiomql import (
                AccountTradeMode,
                Config,
                DealType,
                MetaTrader,
                OrderFilling,
                OrderTime,
                OrderType,
                TimeFrame,
                TradeAction,
                TradeRetcode,
            )
        except (ImportError, ModuleNotFoundError) as error:
            raise RuntimeError(
                "Live MT5 requires Windows, MetaTrader 5, Python 3.13+, and `pip install -e .`"
            ) from error
        return locals()

    async def connect(self) -> None:
        imports = self._imports()
        credentials_path = Path(self.config.credentials_file)
        if not credentials_path.exists():
            raise FileNotFoundError(
                f"Missing {credentials_path}. Copy configs/autotrader.credentials.example.json and fill DEMO credentials."
            )
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
        imports["Config"](**credentials)
        self.mt5 = imports["MetaTrader"]()
        if not await self.mt5.initialize() or not await self.mt5.login():
            error = self.mt5.error
            await self.mt5.shutdown()
            self.mt5 = None
            raise ConnectionError(f"Could not initialize/login to MetaTrader 5: {error}")
        account = await self.mt5.account_info()
        if account is None or int(account.trade_mode) != int(imports["AccountTradeMode"].DEMO):
            await self.mt5.shutdown()
            self.mt5 = None
            raise DemoSafetyError("Hard stop: AutoTrader-MT5 v1 accepts DEMO accounts only")

    async def close(self) -> None:
        if self.mt5 is not None:
            await self.mt5.shutdown()
            self.mt5 = None

    def set_symbol_mapping(self, mapping: dict[str, str]) -> None:
        self._canonical_by_broker = {broker: canonical for canonical, broker in mapping.items()}

    async def available_symbols(self) -> tuple[str, ...]:
        symbols = await self.mt5.symbols_get()
        return tuple(item.name for item in (symbols or ()))

    async def candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        imports = self._imports()
        timeframe_value = getattr(imports["TimeFrame"], timeframe)
        if not await self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Broker refused to select {symbol} in Market Watch")
        rates = await self.mt5.copy_rates_from_pos(symbol, timeframe_value, 1, count)
        if rates is None or len(rates) < count:
            raise RuntimeError(f"Insufficient {timeframe} candles for {symbol}: {0 if rates is None else len(rates)}")
        return [
            Candle(
                time=int(row["time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["tick_volume"]),
            )
            for row in rates
        ]

    async def account(self) -> AccountSnapshot:
        info = await self.mt5.account_info()
        if info is None:
            raise ConnectionError("MetaTrader account_info returned no data")
        if int(info.trade_mode) != 0:
            raise DemoSafetyError("Hard stop: account changed and is no longer DEMO")
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        deals = await self.mt5.history_deals_get(start, now) or ()
        imports = self._imports()
        non_pnl_types = {
            int(imports["DealType"].BALANCE),
            int(imports["DealType"].CREDIT),
            int(imports["DealType"].CORRECTION),
            int(imports["DealType"].BONUS),
        }
        realized = sum(
            float(getattr(deal, "profit", 0.0))
            + float(getattr(deal, "commission", 0.0))
            + float(getattr(deal, "swap", 0.0))
            + float(getattr(deal, "fee", 0.0))
            for deal in deals
            if int(getattr(deal, "type", -1)) not in non_pnl_types
        )
        day_start_balance = float(info.balance) - realized
        daily_pnl = realized + (float(info.equity) - float(info.balance))
        return AccountSnapshot(
            balance=float(info.balance),
            equity=float(info.equity),
            server=str(info.server),
            trade_mode=int(info.trade_mode),
            currency=str(info.currency),
            day_start_balance=day_start_balance,
            daily_pnl=daily_pnl,
        )

    async def symbol_spec(self, symbol: str) -> SymbolSpec:
        info = await self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"No symbol specification for {symbol}")
        return SymbolSpec(
            name=symbol,
            digits=int(info.digits),
            point=float(info.point),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            filling_mode=int(info.filling_mode),
        )

    async def positions(self) -> tuple[Position, ...]:
        raw_positions = await self.mt5.positions_get() or ()
        account = await self.account()
        imports = self._imports()
        result: list[Position] = []
        for item in raw_positions:
            direction = Direction.LONG if int(item.type) == 0 else Direction.SHORT
            canonical = self._canonical_by_broker.get(item.symbol, item.symbol)
            profile = self.config.profile_for(canonical)
            initial_stop = float(item.sl)
            if float(item.tp) > 0 and profile.reward_risk > 0:
                initial_distance = abs(float(item.tp) - float(item.price_open)) / profile.reward_risk
                initial_stop = float(item.price_open) - initial_distance * direction.sign
            risk_percent = 0.0
            stop_is_risk = (
                direction is Direction.LONG and 0 < float(item.sl) < float(item.price_open)
            ) or (
                direction is Direction.SHORT and float(item.sl) > float(item.price_open)
            )
            if stop_is_risk:
                order_type = imports["OrderType"].BUY if direction is Direction.LONG else imports["OrderType"].SELL
                loss = await self.mt5.order_calc_profit(
                    order_type, item.symbol, float(item.volume), float(item.price_open), float(item.sl)
                )
                risk_percent = abs(float(loss or 0.0)) / max(account.equity, 1e-12) * 100
            result.append(
                Position(
                    ticket=int(item.ticket),
                    symbol=str(item.symbol),
                    canonical_symbol=canonical,
                    direction=direction,
                    volume=float(item.volume),
                    entry=float(item.price_open),
                    current_price=float(item.price_current),
                    stop_loss=float(item.sl),
                    take_profit=float(item.tp),
                    group=profile.group,
                    risk_percent=risk_percent,
                    initial_stop_loss=initial_stop,
                    magic=int(getattr(item, "magic", 0)),
                )
            )
        return tuple(result)

    async def volume_for_risk(
        self, symbol: str, direction: Direction, entry: float, stop_loss: float, risk_amount: float
    ) -> float:
        imports = self._imports()
        order_type = imports["OrderType"].BUY if direction is Direction.LONG else imports["OrderType"].SELL
        loss_per_lot = await self.mt5.order_calc_profit(order_type, symbol, 1.0, entry, stop_loss)
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

    def _filling(self, filling_mode: int, imports: dict[str, Any]) -> Any:
        if filling_mode & 2:
            return imports["OrderFilling"].IOC
        if filling_mode & 1:
            return imports["OrderFilling"].FOK
        return imports["OrderFilling"].RETURN

    async def submit(self, request: OrderRequest) -> OrderResult:
        account = await self.account()
        if account.trade_mode != 0:
            raise DemoSafetyError("Hard stop before order_send: non-DEMO account")
        imports = self._imports()
        spec = await self.symbol_spec(request.symbol)
        tick = await self.mt5.symbol_info_tick(request.symbol)
        if tick is None:
            return OrderResult(False, message="No current tick")
        order_type = imports["OrderType"].BUY if request.direction is Direction.LONG else imports["OrderType"].SELL
        price = float(tick.ask if request.direction is Direction.LONG else tick.bid)
        payload = {
            "action": imports["TradeAction"].DEAL,
            "symbol": request.symbol,
            "volume": request.volume,
            "type": order_type,
            "price": round(price, spec.digits),
            "sl": round(request.stop_loss, spec.digits),
            "tp": round(request.take_profit, spec.digits),
            "deviation": 20,
            "magic": self.config.magic_number,
            "comment": request.comment[:25],
            "type_time": imports["OrderTime"].GTC,
            "type_filling": self._filling(spec.filling_mode, imports),
        }
        checked = await self.mt5.order_check(dict(payload))
        if checked is None or int(checked.retcode) != 0:
            return OrderResult(False, message=f"order_check failed: {getattr(checked, 'comment', 'no response')}")
        result = await self.mt5.order_send(payload)
        accepted_codes = {
            int(imports["TradeRetcode"].DONE),
            int(imports["TradeRetcode"].DONE_PARTIAL),
            int(imports["TradeRetcode"].PLACED),
        }
        accepted = result is not None and int(result.retcode) in accepted_codes
        return OrderResult(
            accepted=accepted,
            ticket=int(getattr(result, "order", 0) or getattr(result, "deal", 0) or 0),
            message=str(getattr(result, "comment", "no response")),
            raw={
                "retcode": int(getattr(result, "retcode", -1)),
                "order": int(getattr(result, "order", 0)),
                "deal": int(getattr(result, "deal", 0)),
            },
        )

    async def update_stops(self, ticket: int, symbol: str, stop_loss: float, take_profit: float) -> OrderResult:
        await self.account()
        imports = self._imports()
        spec = await self.symbol_spec(symbol)
        result = await self.mt5.order_send(
            {
                "action": imports["TradeAction"].SLTP,
                "position": ticket,
                "symbol": symbol,
                "sl": round(stop_loss, spec.digits),
                "tp": round(take_profit, spec.digits),
                "magic": self.config.magic_number,
            }
        )
        accepted = result is not None and int(result.retcode) == int(imports["TradeRetcode"].DONE)
        return OrderResult(accepted, ticket=ticket, message=str(getattr(result, "comment", "no response")))

"""Asynchronous multi-asset scanner and order orchestrator."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import logging

from .broker import Broker
from .config import AppConfig
from .management import PositionManager
from .market_data import MarketDataGuard
from .models import OrderRequest, Position, ScoredSignal
from .risk import RiskManager
from .sessions import EntrySessionGuard
from .signals import SignalEngine
from .storage import EventStore
from .symbols import SymbolResolver

logger = logging.getLogger(__name__)


class AutoTrader:
    def __init__(
        self,
        config: AppConfig,
        broker: Broker,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.broker = broker
        self.signal_engine = SignalEngine()
        self.risk_manager = RiskManager(config)
        self.store = EventStore(config.log_directory)
        self.position_manager = PositionManager(config, broker, self.store)
        self.market_data_guard = MarketDataGuard(config.market_data)
        self.session_guard = EntrySessionGuard(config.sessions)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.resolved_symbols: dict[str, str] = {}
        self.execution_blocked_symbols: dict[str, str] = {}
        self._stop = asyncio.Event()

    async def initialize(self) -> None:
        await self.broker.connect()
        account = await self.broker.account()
        if account.trade_mode != 0:
            raise RuntimeError("Hard stop: DEMO account required")
        available = await self.broker.available_symbols()
        resolver = SymbolResolver(self.config.aliases)
        resolved, errors = resolver.resolve_all(self.config.symbols, available)
        tradable: dict[str, str] = {}
        for canonical, broker_symbol in resolved.items():
            try:
                allowed, reason = await self.broker.validate_symbol(broker_symbol)
            except Exception as error:
                allowed, reason = False, f"symbol preflight failed: {error}"
            if allowed:
                tradable[canonical] = broker_symbol
            else:
                errors[canonical] = reason
        if not tradable:
            raise RuntimeError(f"None of the configured symbols could be resolved: {errors}")
        self.resolved_symbols = tradable
        self.broker.set_symbol_mapping(tradable)
        self.store.record("startup", {"account": account, "resolved": tradable, "resolution_errors": errors})
        for symbol, error in errors.items():
            logger.warning("Skipping %s: %s", symbol, error)

    async def _signal_for(self, canonical: str, broker_symbol: str) -> ScoredSignal | None:
        try:
            candle_sets = {
                timeframe: candles
                for timeframe, candles in zip(
                    self.config.timeframes,
                    await asyncio.gather(
                        *(
                            self.broker.candles(broker_symbol, timeframe, self.config.candle_count)
                            for timeframe in self.config.timeframes
                        )
                    ),
                )
            }
            latest_tick_time = await self.broker.latest_tick_time(broker_symbol)
            data_is_fresh, freshness_reason = self.market_data_guard.evaluate(
                candle_sets,
                latest_tick_time,
                self.clock(),
            )
            if not data_is_fresh:
                self.store.record(
                    "market_data_rejection",
                    {"reason": freshness_reason, "broker_symbol": broker_symbol},
                    canonical,
                )
                logger.warning("Skipping %s: %s", canonical, freshness_reason)
                return None
            profile = self.config.profile_for(canonical)
            signal = self.signal_engine.evaluate(
                canonical,
                broker_symbol,
                candle_sets,
                profile.atr_stop_multiplier,
                profile.reward_risk,
            )
            self.store.record("signal", signal, canonical)
            return signal
        except Exception as error:
            self.store.record("scan_error", {"error": str(error), "broker_symbol": broker_symbol}, canonical)
            logger.exception("Scan failed for %s", canonical)
            return None

    async def scan_cycle(self) -> list[ScoredSignal]:
        await self.position_manager.manage()
        signals = await asyncio.gather(
            *(
                self._signal_for(canonical, broker)
                for canonical, broker in self.resolved_symbols.items()
                if canonical not in self.execution_blocked_symbols
            )
        )
        candidates = sorted(
            (signal for signal in signals if signal is not None), key=lambda item: item.score, reverse=True
        )
        account = await self.broker.account()
        positions = list(await self.broker.positions())
        projected_margin = account.margin
        for signal in candidates:
            entry_allowed, session_reason = self.session_guard.evaluate(
                self.config.profile_for(signal.canonical_symbol).group,
                self.clock(),
            )
            if not entry_allowed:
                self.store.record(
                    "session_rejection",
                    {"signal": signal, "reason": session_reason},
                    signal.canonical_symbol,
                )
                logger.warning("Skipping %s: %s", signal.canonical_symbol, session_reason)
                continue
            decision = self.risk_manager.evaluate(signal, account, tuple(positions))
            if not decision.allowed:
                self.store.record("risk_rejection", {"signal": signal, "decision": decision}, signal.canonical_symbol)
                continue
            try:
                entry, stop_loss, take_profit = await self.broker.prepare_order_levels(
                    signal.broker_symbol,
                    signal.direction,
                    signal.stop_loss,
                    signal.take_profit,
                    self.config.profile_for(signal.canonical_symbol).reward_risk,
                )
                volume = await self.broker.volume_for_risk(
                    signal.broker_symbol,
                    signal.direction,
                    entry,
                    stop_loss,
                    decision.risk_amount,
                )
                required_margin = await self.broker.margin_required(
                    signal.broker_symbol, signal.direction, volume, entry
                )
                margin_decision = self.risk_manager.evaluate_deposit_load(
                    account, projected_margin + required_margin
                )
                if not margin_decision.allowed:
                    self.store.record(
                        "risk_rejection",
                        {
                            "signal": signal,
                            "decision": margin_decision,
                            "required_margin": required_margin,
                            "projected_margin": projected_margin + required_margin,
                        },
                        signal.canonical_symbol,
                    )
                    continue
                request = OrderRequest(
                    symbol=signal.broker_symbol,
                    canonical_symbol=signal.canonical_symbol,
                    direction=signal.direction,
                    volume=volume,
                    price=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_amount=decision.risk_amount,
                    score=signal.score,
                )
                result = await self.broker.submit(request)
                self.store.record("order_result", {"request": request, "result": result}, signal.canonical_symbol)
                if not result.accepted and "trade disabled" in result.message.lower():
                    self.execution_blocked_symbols[signal.canonical_symbol] = result.message
                    self.store.record(
                        "symbol_blocked",
                        {"reason": result.message, "broker_symbol": signal.broker_symbol},
                        signal.canonical_symbol,
                    )
                    logger.warning(
                        "Blocking %s until restart: %s", signal.canonical_symbol, result.message
                    )
                if result.accepted:
                    projected_margin += required_margin
                    profile = self.config.profile_for(signal.canonical_symbol)
                    positions.append(
                        Position(
                            ticket=result.ticket,
                            symbol=signal.broker_symbol,
                            canonical_symbol=signal.canonical_symbol,
                            direction=signal.direction,
                            volume=volume,
                            entry=entry,
                            current_price=entry,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            group=profile.group,
                            risk_percent=decision.risk_percent,
                            initial_stop_loss=stop_loss,
                            magic=self.config.magic_number,
                        )
                    )
            except Exception as error:
                self.store.record("order_error", {"signal": signal, "error": str(error)}, signal.canonical_symbol)
                logger.exception("Order preparation/submission failed for %s", signal.canonical_symbol)
        return candidates

    async def run(self, once: bool = False) -> None:
        try:
            await self.initialize()
            while not self._stop.is_set():
                await self.scan_cycle()
                if once:
                    return
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.config.scan_interval_seconds)
                except TimeoutError:
                    pass
        finally:
            self.store.record("shutdown", {"reason": "normal"})
            await self.broker.close()

    def stop(self) -> None:
        self._stop.set()

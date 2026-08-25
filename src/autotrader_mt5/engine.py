"""Asynchronous multi-asset scanner and order orchestrator."""

from __future__ import annotations

import asyncio
import logging

from .broker import Broker
from .config import AppConfig
from .management import PositionManager
from .models import OrderRequest, Position, ScoredSignal
from .risk import RiskManager
from .signals import SignalEngine
from .storage import EventStore
from .symbols import SymbolResolver

logger = logging.getLogger(__name__)


class AutoTrader:
    def __init__(self, config: AppConfig, broker: Broker):
        self.config = config
        self.broker = broker
        self.signal_engine = SignalEngine()
        self.risk_manager = RiskManager(config)
        self.store = EventStore(config.log_directory)
        self.position_manager = PositionManager(config, broker, self.store)
        self.resolved_symbols: dict[str, str] = {}
        self._stop = asyncio.Event()

    async def initialize(self) -> None:
        await self.broker.connect()
        account = await self.broker.account()
        if account.trade_mode != 0:
            raise RuntimeError("Hard stop: DEMO account required")
        available = await self.broker.available_symbols()
        resolver = SymbolResolver(self.config.aliases)
        resolved, errors = resolver.resolve_all(self.config.symbols, available)
        if not resolved:
            raise RuntimeError(f"None of the configured symbols could be resolved: {errors}")
        self.resolved_symbols = resolved
        self.broker.set_symbol_mapping(resolved)
        self.store.record("startup", {"account": account, "resolved": resolved, "resolution_errors": errors})
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
            *(self._signal_for(canonical, broker) for canonical, broker in self.resolved_symbols.items())
        )
        candidates = sorted(
            (signal for signal in signals if signal is not None), key=lambda item: item.score, reverse=True
        )
        account = await self.broker.account()
        positions = list(await self.broker.positions())
        for signal in candidates:
            decision = self.risk_manager.evaluate(signal, account, tuple(positions))
            if not decision.allowed:
                self.store.record("risk_rejection", {"signal": signal, "decision": decision}, signal.canonical_symbol)
                continue
            try:
                volume = await self.broker.volume_for_risk(
                    signal.broker_symbol,
                    signal.direction,
                    signal.entry,
                    signal.stop_loss,
                    decision.risk_amount,
                )
                request = OrderRequest(
                    symbol=signal.broker_symbol,
                    canonical_symbol=signal.canonical_symbol,
                    direction=signal.direction,
                    volume=volume,
                    price=signal.entry,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    risk_amount=decision.risk_amount,
                    score=signal.score,
                )
                result = await self.broker.submit(request)
                self.store.record("order_result", {"request": request, "result": result}, signal.canonical_symbol)
                if result.accepted:
                    profile = self.config.profile_for(signal.canonical_symbol)
                    positions.append(
                        Position(
                            ticket=result.ticket,
                            symbol=signal.broker_symbol,
                            canonical_symbol=signal.canonical_symbol,
                            direction=signal.direction,
                            volume=volume,
                            entry=signal.entry,
                            current_price=signal.entry,
                            stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit,
                            group=profile.group,
                            risk_percent=decision.risk_percent,
                            initial_stop_loss=signal.stop_loss,
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

"""Signal composition and 0-100 scoring shared by live and backtest."""

from __future__ import annotations

from .indicators import atr, clamp
from .models import Candle, Direction, ScoredSignal, SignalVote
from .strategies import DEFAULT_STRATEGIES, Strategy


class SignalEngine:
    def __init__(self, strategies: tuple[Strategy, ...] = DEFAULT_STRATEGIES):
        self.strategies = strategies

    def evaluate(
        self,
        canonical_symbol: str,
        broker_symbol: str,
        candles_by_timeframe: dict[str, list[Candle]],
        atr_stop_multiplier: float,
        reward_risk: float,
    ) -> ScoredSignal:
        votes: list[SignalVote] = []
        for timeframe, candles in candles_by_timeframe.items():
            timeframe_weight = 1.1 if timeframe == "M15" else 1.0
            for strategy in self.strategies:
                vote = strategy.evaluate(candles, timeframe)
                votes.append(
                    SignalVote(
                        vote.strategy,
                        vote.timeframe,
                        vote.direction,
                        vote.strength,
                        vote.reason,
                        vote.weight * timeframe_weight,
                    )
                )

        active = [vote for vote in votes if vote.direction is not Direction.FLAT and vote.strength > 0]
        if not active:
            direction, score = Direction.FLAT, 0
        else:
            net = sum(vote.direction.sign * vote.strength * vote.weight for vote in active)
            total = sum(vote.strength * vote.weight for vote in active)
            direction = Direction.LONG if net > 0 else Direction.SHORT if net < 0 else Direction.FLAT
            aligned = [vote for vote in active if vote.direction is direction]
            alignment = abs(net) / total if total else 0.0
            conviction = sum(vote.strength * vote.weight for vote in aligned) / sum(vote.weight for vote in aligned)
            coverage = len(active) / max(len(votes), 1)
            score = round(100 * clamp(conviction * alignment * (0.75 + 0.25 * coverage)))

        primary = candles_by_timeframe.get("M5") or next(iter(candles_by_timeframe.values()))
        entry = primary[-1].close
        volatility = atr(primary)
        distance = volatility * atr_stop_multiplier
        if direction is Direction.LONG:
            stop_loss, take_profit = entry - distance, entry + distance * reward_risk
        elif direction is Direction.SHORT:
            stop_loss, take_profit = entry + distance, entry - distance * reward_risk
        else:
            stop_loss = take_profit = entry
        return ScoredSignal(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            direction=direction,
            score=max(0, min(100, score)),
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=volatility,
            votes=tuple(votes),
            timestamp=primary[-1].time,
        )

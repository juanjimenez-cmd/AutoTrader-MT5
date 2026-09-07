"""Conservative multi-timeframe trend-breakout signals shared by live and backtest."""

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

        direction, score = self._trend_breakout_decision(votes)

        primary = candles_by_timeframe.get("M5") or next(iter(candles_by_timeframe.values()))
        entry = primary[-1].close
        # The higher M15 volatility prevents a very small M5 stop from turning
        # normal noise into a full loss.
        volatility = atr(candles_by_timeframe.get("M15", primary))
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

    @staticmethod
    def _trend_breakout_decision(votes: list[SignalVote]) -> tuple[Direction, int]:
        """Require trend context, M15 confirmation, and an M5 execution trigger.

        This deliberately rejects the old "net vote" behaviour: a counter-trend
        mean-reversion or a single fast indicator can no longer create a trade.
        """
        indexed = {(vote.strategy, vote.timeframe): vote for vote in votes}
        h1_trend = indexed.get(("trend", "H1"))
        m15_trend = indexed.get(("trend", "M15"))
        m15_breakout = indexed.get(("breakout", "M15"))
        m5_momentum = indexed.get(("momentum", "M5"))
        required = (h1_trend, m15_trend, m15_breakout, m5_momentum)
        if any(vote is None or vote.direction is Direction.FLAT for vote in required):
            return Direction.FLAT, 0

        direction = h1_trend.direction
        if any(vote.direction is not direction for vote in required):
            return Direction.FLAT, 0
        if h1_trend.strength < 0.45 or m15_trend.strength < 0.35 or m15_breakout.strength < 0.55:
            return Direction.FLAT, 0
        if m5_momentum.strength < 0.25:
            return Direction.FLAT, 0

        score = round(
            100
            * clamp(
                0.40 * h1_trend.strength
                + 0.20 * m15_trend.strength
                + 0.30 * m15_breakout.strength
                + 0.10 * m5_momentum.strength
            )
        )
        return direction, score

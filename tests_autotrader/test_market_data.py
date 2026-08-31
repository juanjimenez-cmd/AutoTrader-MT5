from datetime import datetime, timezone
import unittest

from autotrader_mt5.config import MarketDataConfig
from autotrader_mt5.market_data import MarketDataGuard
from autotrader_mt5.models import Candle


class MarketDataGuardTests(unittest.TestCase):
    def setUp(self):
        self.config = MarketDataConfig()
        self.guard = MarketDataGuard(self.config)
        self.now = datetime(2026, 8, 31, 4, 10, tzinfo=timezone.utc)
        self.now_timestamp = int(self.now.timestamp())

    @staticmethod
    def candle(timestamp: int) -> Candle:
        return Candle(timestamp, 1.0, 1.1, 0.9, 1.0)

    def test_accepts_fresh_tick_and_closed_bars(self):
        candles = {
            "M5": [self.candle(self.now_timestamp - 350)],
            "M15": [self.candle(self.now_timestamp - 1_550)],
        }
        allowed, _ = self.guard.evaluate(candles, self.now_timestamp - 1, self.now)
        self.assertTrue(allowed)

    def test_rejects_stale_tick(self):
        candles = {"M5": [self.candle(self.now_timestamp - 350)]}
        allowed, reason = self.guard.evaluate(
            candles,
            self.now_timestamp - self.config.max_tick_age_seconds - 1,
            self.now,
        )
        self.assertFalse(allowed)
        self.assertIn("tick is stale", reason)

    def test_rejects_stale_candle(self):
        maximum_age = 2 * 300 + self.config.closed_bar_grace_seconds
        candles = {"M5": [self.candle(self.now_timestamp - maximum_age - 1)]}
        allowed, reason = self.guard.evaluate(candles, self.now_timestamp, self.now)
        self.assertFalse(allowed)
        self.assertIn("M5 candle is stale", reason)

    def test_rejects_future_tick_after_tolerance(self):
        candles = {"M5": [self.candle(self.now_timestamp - 300)]}
        allowed, reason = self.guard.evaluate(
            candles,
            self.now_timestamp + self.config.future_tolerance_seconds + 1,
            self.now,
        )
        self.assertFalse(allowed)
        self.assertIn("tick is", reason)
        self.assertIn("future", reason)

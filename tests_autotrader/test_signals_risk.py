from dataclasses import replace
import unittest

from autotrader_mt5.models import AccountSnapshot, Direction, Position
from autotrader_mt5.risk import RiskManager
from autotrader_mt5.signals import SignalEngine
from tests_autotrader.helpers import rising_candles, test_config


class SignalAndRiskTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(test_config(), min_score=30)
        candles = rising_candles()
        self.signal = SignalEngine().evaluate(
            "EURUSD", "EURUSD.a", {"M5": candles, "M15": candles}, 1.5, 2.0
        )

    def test_score_and_mandatory_stops(self):
        self.assertEqual(self.signal.direction, Direction.LONG)
        self.assertGreaterEqual(self.signal.score, 30)
        self.assertLess(self.signal.stop_loss, self.signal.entry)
        self.assertGreater(self.signal.take_profit, self.signal.entry)

    def test_real_account_is_rejected(self):
        decision = RiskManager(self.config).evaluate(
            self.signal, AccountSnapshot(10_000, 10_000, "Broker-Live", 2), ()
        )
        self.assertFalse(decision.allowed)
        self.assertIn("not MetaTrader DEMO", decision.reason)

    def test_group_limit_is_enforced(self):
        position = Position(1, "GBPUSD.a", "GBPUSD", Direction.LONG, 0.1, 1, 1, 0.9, 1.2, "usd", 1.2, 0.9)
        decision = RiskManager(self.config).evaluate(
            self.signal, AccountSnapshot(10_000, 10_000, "Broker-Demo", 0), (position,)
        )
        self.assertFalse(decision.allowed)
        self.assertIn("correlation-group", decision.reason)

    def test_persisted_daily_loss_survives_process_restart(self):
        account = AccountSnapshot(
            9_800, 9_750, "Broker-Demo", 0, day_start_balance=10_000, daily_pnl=-250
        )
        decision = RiskManager(self.config).evaluate(self.signal, account, ())
        self.assertFalse(decision.allowed)
        self.assertIn("daily loss limit", decision.reason)

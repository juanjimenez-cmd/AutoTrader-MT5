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
        config = replace(
            self.config,
            risk=replace(self.config.risk, max_simultaneous_risk_percent=2.0),
        )
        position = Position(1, "GBPUSD.a", "GBPUSD", Direction.LONG, 0.1, 1, 1, 0.9, 1.2, "usd", 0.45, 0.9)
        decision = RiskManager(config).evaluate(
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

    def test_new_order_cannot_overshoot_remaining_daily_budget(self):
        account = AccountSnapshot(
            9_805, 9_805, "Broker-Demo", 0, day_start_balance=10_000, daily_pnl=-195
        )
        decision = RiskManager(self.config).evaluate(self.signal, account, ())
        self.assertFalse(decision.allowed)
        self.assertIn("daily loss budget", decision.reason)

    def test_open_risk_is_reserved_from_daily_budget(self):
        position = Position(
            1, "GOLD.a", "XAUUSD", Direction.LONG, 0.1, 1, 1, 0.9, 1.2, "usd", 0.1, 0.9
        )
        account = AccountSnapshot(
            9_810, 9_810, "Broker-Demo", 0, day_start_balance=10_000, daily_pnl=-190
        )
        decision = RiskManager(self.config).evaluate(self.signal, account, (position,))
        self.assertFalse(decision.allowed)
        self.assertIn("daily loss budget", decision.reason)

    def test_projected_deposit_load_is_limited(self):
        account = AccountSnapshot(10_000, 10_000, "Broker-Demo", 0, margin=2_000)
        manager = RiskManager(self.config)
        self.assertTrue(manager.evaluate_deposit_load(account, 2_500).allowed)
        decision = manager.evaluate_deposit_load(account, 2_501)
        self.assertFalse(decision.allowed)
        self.assertIn("deposit load", decision.reason)

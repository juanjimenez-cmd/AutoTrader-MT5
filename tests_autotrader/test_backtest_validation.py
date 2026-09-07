import unittest

from autotrader_mt5.backtest import Backtester
from tests_autotrader.helpers import rising_candles, test_config


class BacktestValidationTests(unittest.TestCase):
    def test_friction_estimate_includes_spread_slippage_and_commission(self):
        tester = Backtester(test_config())
        cost = tester._friction_cost(100.0, 1.1000, 1.0990)
        self.assertGreater(cost, 0.0)

    def test_walk_forward_keeps_forward_entries_after_the_split_only(self):
        candles = rising_candles(1_000)
        report = Backtester(test_config()).run_walk_forward("EURUSD", candles, candles[700].time)
        self.assertEqual(report.forward_start, candles[700].time)
        self.assertTrue(
            all(trade.entry_time >= report.forward_start for trade in report.forward.trades)
        )
        self.assertIn('"forward"', report.to_json())


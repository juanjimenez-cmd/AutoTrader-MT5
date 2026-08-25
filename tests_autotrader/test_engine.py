from dataclasses import replace
import shutil
import unittest

from autotrader_mt5.engine import AutoTrader
from tests_autotrader.helpers import FakeBroker, test_config


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = replace(test_config(), symbols=("EURUSD", "NASDAQ"), min_score=30)
        shutil.rmtree(self.config.log_directory, ignore_errors=True)

    async def asyncTearDown(self):
        shutil.rmtree(self.config.log_directory, ignore_errors=True)

    async def test_one_cycle_resolves_scans_and_places_demo_orders(self):
        broker = FakeBroker()
        engine = AutoTrader(self.config, broker)
        await engine.run(once=True)
        self.assertEqual(engine.resolved_symbols, {"EURUSD": "EURUSD.a", "NASDAQ": "USTEC.a"})
        self.assertGreaterEqual(len(broker.orders), 1)
        self.assertTrue(all(order.stop_loss > 0 and order.take_profit > 0 for order in broker.orders))
        self.assertFalse(broker.connected)

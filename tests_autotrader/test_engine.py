from dataclasses import replace
from datetime import datetime, timezone
import shutil
import unittest

from autotrader_mt5.engine import AutoTrader
from tests_autotrader.helpers import FakeBroker, test_config


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = replace(test_config(), symbols=("EURUSD", "NASDAQ"), min_score=30)
        self.clock = lambda: datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
        shutil.rmtree(self.config.log_directory, ignore_errors=True)

    async def asyncTearDown(self):
        shutil.rmtree(self.config.log_directory, ignore_errors=True)

    async def test_one_cycle_resolves_scans_and_places_demo_orders(self):
        broker = FakeBroker()
        engine = AutoTrader(self.config, broker, clock=self.clock)
        await engine.run(once=True)
        self.assertEqual(engine.resolved_symbols, {"EURUSD": "EURUSD.a", "NASDAQ": "USTEC.a"})
        self.assertGreaterEqual(len(broker.orders), 1)
        self.assertTrue(all(order.stop_loss > 0 and order.take_profit > 0 for order in broker.orders))
        self.assertFalse(broker.connected)

    async def test_disabled_broker_symbol_is_removed_before_scanning(self):
        broker = FakeBroker()
        broker.disabled_symbols.add("USTEC.a")
        engine = AutoTrader(self.config, broker, clock=self.clock)
        await engine.run(once=True)
        self.assertEqual(engine.resolved_symbols, {"EURUSD": "EURUSD.a"})
        self.assertTrue(all(order.canonical_symbol == "EURUSD" for order in broker.orders))

    async def test_trade_disabled_order_result_blocks_retries_until_restart(self):
        config = replace(self.config, symbols=("EURUSD",))
        broker = FakeBroker()
        broker.submit_rejection = "order_check failed: Trade disabled"
        engine = AutoTrader(config, broker, clock=self.clock)
        await engine.initialize()
        await engine.scan_cycle()
        first_attempts = len(broker.orders)
        await engine.scan_cycle()
        await broker.close()
        self.assertGreater(first_attempts, 0)
        self.assertEqual(len(broker.orders), first_attempts)
        self.assertIn("EURUSD", engine.execution_blocked_symbols)

    async def test_weekend_guard_blocks_new_forex_orders(self):
        config = replace(self.config, symbols=("EURUSD",))
        broker = FakeBroker()
        friday_after_cutoff = lambda: datetime(2026, 8, 28, 20, 30, tzinfo=timezone.utc)
        engine = AutoTrader(config, broker, clock=friday_after_cutoff)
        await engine.run(once=True)
        self.assertEqual(broker.orders, [])

    async def test_weekend_guard_does_not_block_crypto_group(self):
        config = replace(self.config, symbols=("BTCUSD",))
        broker = FakeBroker()
        saturday = lambda: datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
        engine = AutoTrader(config, broker, clock=saturday)
        await engine.run(once=True)
        self.assertGreaterEqual(len(broker.orders), 1)

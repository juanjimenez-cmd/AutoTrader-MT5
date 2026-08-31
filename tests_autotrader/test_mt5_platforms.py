from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest

from autotrader_mt5.models import Direction
from autotrader_mt5.mt5_adapter import DemoSafetyError, MT5Broker
from autotrader_mt5.mt5_runtime import MT5Runtime
from tests_autotrader.helpers import ROOT, test_config


class FakeNativeAPI:
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self):
        self.initialize_kwargs = None
        self.login_args = None
        self.closed = False

    def initialize(self, **kwargs):
        self.initialize_kwargs = kwargs
        return True

    def login(self, login, **kwargs):
        self.login_args = (login, kwargs)
        return True

    def shutdown(self):
        self.closed = True

    def last_error(self):
        return (0, "ok")


class FakeBrokerRuntime:
    def __init__(self, trade_mode=0):
        self.backend = "native"
        self.trade_mode = trade_mode
        self.symbol_trade_mode = 4
        self.symbol_description = "Euro vs US Dollar"
        self.symbol_path = "Forex\\EURUSD"
        self.symbol_currency_base = "EUR"
        self.symbol_currency_profit = "USD"
        self.connected = False
        self.closed = False
        self.constants = {
            "ACCOUNT_TRADE_MODE_DEMO": 0,
            "TIMEFRAME_M5": 5,
            "SYMBOL_TRADE_MODE_DISABLED": 0,
            "SYMBOL_TRADE_MODE_LONGONLY": 1,
            "SYMBOL_TRADE_MODE_SHORTONLY": 2,
            "SYMBOL_TRADE_MODE_CLOSEONLY": 3,
            "SYMBOL_TRADE_MODE_FULL": 4,
            "ORDER_TYPE_BUY": 0,
            "ORDER_TYPE_SELL": 1,
        }

    def connect(self, credentials):
        self.connected = bool(credentials["login"])

    def close(self):
        self.closed = True

    def constant(self, name, default=None):
        if name in self.constants:
            return self.constants[name]
        if default is not None:
            return default
        raise KeyError(name)

    def call(self, name, *args, **kwargs):
        if name == "account_info":
            return SimpleNamespace(trade_mode=self.trade_mode)
        if name == "symbol_select":
            return True
        if name == "copy_rates_from_pos":
            return [
                {"time": 1, "open": 10, "high": 12, "low": 9, "close": 11, "tick_volume": 100},
                SimpleNamespace(time=2, open=11, high=13, low=10, close=12, tick_volume=110),
            ]
        if name == "symbol_info":
            return SimpleNamespace(
                name=args[0], digits=5, point=0.00001, volume_min=0.01, volume_max=100.0,
                volume_step=0.01, filling_mode=1, trade_mode=self.symbol_trade_mode,
                trade_stops_level=20, trade_freeze_level=0, trade_tick_size=0.00001,
                description=self.symbol_description, path=self.symbol_path,
                currency_base=self.symbol_currency_base, currency_profit=self.symbol_currency_profit,
            )
        if name == "symbol_info_tick":
            return SimpleNamespace(ask=1.10020, bid=1.10000, time=1_788_149_450)
        if name == "order_calc_margin":
            return 250.0
        raise AssertionError(name)


class FakeBridgeModule:
    def __init__(self):
        self.init_kwargs = None
        self.closed = False

    def init(self, **kwargs):
        self.init_kwargs = kwargs
        return SimpleNamespace(mt5=SimpleNamespace())

    def shutdown(self, handle):
        self.closed = handle is not None


class PlatformRuntimeTests(unittest.TestCase):
    def config_with_test_credentials(self):
        return replace(
            test_config(),
            credentials_file=ROOT / "configs" / "autotrader.credentials.example.json",
        )

    def test_auto_selects_native_on_windows_and_bridge_on_macos(self):
        self.assertEqual(MT5Runtime("auto", system_name="Windows").backend, "native")
        self.assertEqual(MT5Runtime("auto", system_name="Darwin").backend, "bridge")

    def test_native_runtime_initializes_and_logs_in(self):
        api = FakeNativeAPI()
        runtime = MT5Runtime("native", system_name="Windows", module_loader=lambda name: api)
        runtime.connect(
            {"login": 1234, "password": "secret", "server": "Broker-Demo", "path": "terminal64.exe"}
        )
        self.assertEqual(runtime.api, api)
        self.assertEqual(api.initialize_kwargs["login"], 1234)
        self.assertEqual(api.login_args[1]["server"], "Broker-Demo")
        runtime.close()
        self.assertTrue(api.closed)

    def test_bridge_runtime_uses_configured_local_endpoint(self):
        bridge = FakeBridgeModule()
        runtime = MT5Runtime(
            "bridge",
            bridge_host="127.0.0.1",
            bridge_port=18813,
            system_name="Darwin",
            module_loader=lambda name: bridge,
        )
        runtime.connect({"login": 1234, "password": "secret", "server": "Broker-Demo"})
        self.assertEqual(bridge.init_kwargs["backend"], "bridge")
        self.assertEqual(bridge.init_kwargs["port"], 18813)
        self.assertFalse(bridge.init_kwargs["register"])
        runtime.close()
        self.assertTrue(bridge.closed)

    def test_broker_accepts_demo_and_normalizes_rate_shapes(self):
        runtime = FakeBrokerRuntime()
        broker = MT5Broker(self.config_with_test_credentials(), runtime=runtime)

        async def exercise():
            await broker.connect()
            candles = await broker.candles("EURUSD", "M5", 2)
            await broker.close()
            return candles

        candles = asyncio.run(exercise())
        self.assertTrue(runtime.connected)
        self.assertEqual([item.close for item in candles], [11.0, 12.0])
        self.assertTrue(runtime.closed)

    def test_broker_rejects_real_account_on_every_platform(self):
        runtime = FakeBrokerRuntime(trade_mode=2)
        broker = MT5Broker(self.config_with_test_credentials(), runtime=runtime)
        with self.assertRaisesRegex(DemoSafetyError, "DEMO"):
            asyncio.run(broker.connect())
        self.assertTrue(runtime.closed)

    def test_symbol_preflight_rejects_disabled_markets(self):
        runtime = FakeBrokerRuntime()
        runtime.symbol_trade_mode = 0
        broker = MT5Broker(self.config_with_test_credentials(), runtime=runtime)
        allowed, reason = asyncio.run(broker.validate_symbol("EURUSD"))
        self.assertFalse(allowed)
        self.assertIn("not open for new trades", reason)

    def test_xauusd_preflight_rejects_ambiguous_gold_stock(self):
        runtime = FakeBrokerRuntime()
        runtime.symbol_description = "Barrick Gold Corporation (BC)"
        runtime.symbol_path = "Nasdaq\\Stock\\GOLD"
        runtime.symbol_currency_base = "USD"
        broker = MT5Broker(self.config_with_test_credentials(), runtime=runtime)
        allowed, reason = asyncio.run(
            broker.validate_symbol("GOLD", canonical_symbol="XAUUSD")
        )
        self.assertFalse(allowed)
        self.assertIn("does not match XAUUSD spot gold", reason)

    def test_xauusd_preflight_accepts_spot_metal(self):
        runtime = FakeBrokerRuntime()
        runtime.symbol_description = "Gold vs US Dollar"
        runtime.symbol_path = "Metals\\XAUUSD"
        runtime.symbol_currency_base = "XAU"
        broker = MT5Broker(self.config_with_test_credentials(), runtime=runtime)
        allowed, _ = asyncio.run(
            broker.validate_symbol("XAUUSD", canonical_symbol="XAUUSD")
        )
        self.assertTrue(allowed)

    def test_order_levels_respect_broker_stop_distance_and_margin_is_calculated(self):
        runtime = FakeBrokerRuntime()
        broker = MT5Broker(self.config_with_test_credentials(), runtime=runtime)

        async def exercise():
            levels = await broker.prepare_order_levels("EURUSD", Direction.LONG, 1.10010, 1.10030, 2.0)
            margin = await broker.margin_required("EURUSD", Direction.LONG, 0.10, levels[0])
            return levels, margin

        (price, stop_loss, take_profit), margin = asyncio.run(exercise())
        self.assertLessEqual(stop_loss, round(price - 0.00022, 5))
        self.assertGreaterEqual(take_profit, round(price + 0.00022, 5))
        self.assertGreaterEqual(take_profit - price, 2 * (price - stop_loss) - 1e-9)
        self.assertEqual(margin, 250.0)

    def test_native_tick_timestamp_remains_utc(self):
        runtime = FakeBrokerRuntime()
        broker = MT5Broker(self.config_with_test_credentials(), runtime=runtime)
        self.assertEqual(asyncio.run(broker.latest_tick_time("EURUSD")), 1_788_149_450)

    def test_bridge_server_wall_clock_is_normalized_to_utc(self):
        runtime = FakeBrokerRuntime()
        runtime.backend = "bridge"
        broker = MT5Broker(self.config_with_test_credentials(), runtime=runtime)
        # Europe/Helsinki is UTC+3 in August 2026.
        self.assertEqual(asyncio.run(broker.latest_tick_time("EURUSD")), 1_788_138_650)


if __name__ == "__main__":
    unittest.main()

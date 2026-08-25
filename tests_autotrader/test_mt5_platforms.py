from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest

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
        self.trade_mode = trade_mode
        self.connected = False
        self.closed = False
        self.constants = {
            "ACCOUNT_TRADE_MODE_DEMO": 0,
            "TIMEFRAME_M5": 5,
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


if __name__ == "__main__":
    unittest.main()

from dataclasses import replace
import unittest

from autotrader_mt5.symbols import SymbolResolver
from tests_autotrader.helpers import test_config


class ConfigAndSymbolTests(unittest.TestCase):
    def test_demo_only_cannot_be_disabled(self):
        with self.assertRaisesRegex(ValueError, "demo_only"):
            replace(test_config(), demo_only=False)

    def test_platform_transport_defaults_to_auto(self):
        config = test_config()
        self.assertEqual(config.mt5.backend, "auto")
        self.assertEqual(config.mt5.bridge_port, 18813)

    def test_conservative_demo_risk_defaults(self):
        config = test_config()
        self.assertEqual(config.risk.default_risk_percent, 0.10)
        self.assertEqual(config.risk.max_deposit_load_percent, 25.0)
        self.assertEqual(config.risk.max_positions, 2)
        self.assertEqual(config.risk.max_group_risk_percent["usd"], 0.50)
        self.assertTrue(config.sessions.weekend_guard_enabled)
        self.assertEqual(config.sessions.friday_entry_cutoff_utc, "20:30")
        self.assertEqual(config.sessions.sunday_entry_resume_utc, "22:30")
        self.assertEqual(config.market_data.max_tick_age_seconds, 120)
        self.assertEqual(config.market_data.bridge_server_timezone, "Europe/Helsinki")
        self.assertIn("GBPUSD", config.symbols)
        self.assertIn("USDJPY", config.symbols)
        self.assertNotIn("BTCUSD", config.symbols)
        self.assertNotIn("ETHUSD", config.symbols)

    def test_resolves_common_broker_names(self):
        config = test_config()
        resolver = SymbolResolver(config.aliases)
        available = ("EURUSD.a", "GOLDmicro", "USTEC.cash", "US500-USD", "BTCUSD.a")
        self.assertEqual(resolver.resolve("EURUSD", available), "EURUSD.a")
        self.assertEqual(resolver.resolve("XAUUSD", available), "GOLDmicro")
        self.assertEqual(resolver.resolve("NASDAQ", available), "USTEC.cash")
        self.assertEqual(resolver.resolve("SP500", available), "US500-USD")

    def test_primary_xauusd_alias_beats_ambiguous_gold_stock(self):
        resolver = SymbolResolver(test_config().aliases)
        self.assertEqual(resolver.resolve("XAUUSD", ("GOLD", "XAUUSD")), "XAUUSD")
        self.assertEqual(resolver.resolve("XAUUSD", ("GOLD", "XAUUSD.a")), "XAUUSD.a")

from dataclasses import replace
import unittest

from autotrader_mt5.symbols import SymbolResolver
from tests_autotrader.helpers import test_config


class ConfigAndSymbolTests(unittest.TestCase):
    def test_demo_only_cannot_be_disabled(self):
        with self.assertRaisesRegex(ValueError, "demo_only"):
            replace(test_config(), demo_only=False)

    def test_resolves_common_broker_names(self):
        config = test_config()
        resolver = SymbolResolver(config.aliases)
        available = ("EURUSD.a", "GOLDmicro", "USTEC.cash", "US500-USD", "BTCUSD.a")
        self.assertEqual(resolver.resolve("EURUSD", available), "EURUSD.a")
        self.assertEqual(resolver.resolve("XAUUSD", available), "GOLDmicro")
        self.assertEqual(resolver.resolve("NASDAQ", available), "USTEC.cash")
        self.assertEqual(resolver.resolve("SP500", available), "US500-USD")

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import unittest

from autotrader_mt5.config import EntryControlConfig
from autotrader_mt5.entry_controls import EntryThrottle
from autotrader_mt5.storage import EventStore


class EntryThrottleTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path("work-test-entry-controls")
        shutil.rmtree(self.directory, ignore_errors=True)
        self.store = EventStore(self.directory)
        self.throttle = EntryThrottle(EntryControlConfig(cooldown_minutes=90, max_entries_per_symbol_day=2), self.store)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_cooldown_blocks_a_reentry_after_an_accepted_order(self):
        now = datetime.now(timezone.utc)
        self.store.record("order_result", {"result": {"accepted": True}}, "EURUSD")
        allowed, reason = self.throttle.evaluate("EURUSD", now)
        self.assertFalse(allowed)
        self.assertIn("cooldown", reason)

    def test_daily_limit_blocks_third_accepted_order(self):
        self.store.record("order_result", {"result": {"accepted": True}}, "EURUSD")
        self.store.record("order_result", {"result": {"accepted": True}}, "EURUSD")
        allowed, reason = self.throttle.evaluate("EURUSD", datetime.now(timezone.utc) + timedelta(minutes=91))
        self.assertFalse(allowed)
        self.assertIn("daily entry limit", reason)

    def test_other_symbol_is_not_affected(self):
        self.store.record("order_result", {"result": {"accepted": True}}, "EURUSD")
        allowed, _ = self.throttle.evaluate("XAUUSD", datetime.now(timezone.utc))
        self.assertTrue(allowed)

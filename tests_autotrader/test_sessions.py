from datetime import datetime, timezone
import unittest

from autotrader_mt5.config import SessionConfig
from autotrader_mt5.sessions import EntrySessionGuard


class EntrySessionGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = EntrySessionGuard(SessionConfig())

    def evaluate(self, group: str, weekday: int, hour: int, minute: int = 0) -> bool:
        # 2026-08-24 is a Monday.
        now = datetime(2026, 8, 24 + weekday, hour, minute, tzinfo=timezone.utc)
        return self.guard.evaluate(group, now)[0]

    def test_guarded_group_is_open_before_friday_cutoff(self):
        self.assertTrue(self.evaluate("usd", 4, 20, 29))

    def test_guarded_group_closes_at_friday_cutoff(self):
        self.assertFalse(self.evaluate("usd", 4, 20, 30))

    def test_guarded_group_remains_closed_saturday(self):
        self.assertFalse(self.evaluate("us_indices", 5, 12))

    def test_guarded_group_reopens_at_sunday_resume(self):
        self.assertFalse(self.evaluate("usd", 6, 22, 29))
        self.assertTrue(self.evaluate("usd", 6, 22, 30))

    def test_crypto_is_not_blocked_by_weekend_guard(self):
        self.assertTrue(self.evaluate("crypto", 5, 12))

    def test_naive_datetime_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.guard.evaluate("usd", datetime(2026, 8, 28, 20, 30))

from datetime import datetime, timezone
import unittest

from autotrader_mt5.config import EntrySchedule, SessionConfig
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


class IntradayEntryScheduleTests(unittest.TestCase):
    def setUp(self):
        self.guard = EntrySessionGuard(
            SessionConfig(
                entry_schedules={
                    "EURUSD": EntrySchedule("America/Guayaquil", ("09:00-11:00",)),
                    "USDJPY": EntrySchedule("America/Guayaquil", ("09:00-11:00", "18:00-23:00")),
                    "NASDAQ": EntrySchedule("America/New_York", ("10:00-12:30",)),
                }
            )
        )

    def test_quito_schedule_allows_only_the_configured_window(self):
        before = datetime(2026, 8, 24, 13, 59, tzinfo=timezone.utc)  # 08:59 Quito
        inside = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)  # 09:00 Quito
        after = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)  # 11:00 Quito
        self.assertFalse(self.guard.evaluate("usd", before, canonical_symbol="EURUSD")[0])
        self.assertTrue(self.guard.evaluate("usd", inside, canonical_symbol="EURUSD")[0])
        self.assertFalse(self.guard.evaluate("usd", after, canonical_symbol="EURUSD")[0])

    def test_new_york_schedule_tracks_daylight_saving_time(self):
        summer_inside = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)  # 10:00 EDT
        winter_inside = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)  # 10:00 EST
        self.assertTrue(self.guard.evaluate("us_indices", summer_inside, canonical_symbol="NASDAQ")[0])
        self.assertTrue(self.guard.evaluate("us_indices", winter_inside, canonical_symbol="NASDAQ")[0])

    def test_usdjpy_evening_window_is_allowed_in_quito(self):
        evening = datetime(2026, 8, 24, 23, 0, tzinfo=timezone.utc)  # 18:00 Quito
        after = datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc)  # 23:00 Quito
        self.assertTrue(self.guard.evaluate("usd", evening, canonical_symbol="USDJPY")[0])
        self.assertFalse(self.guard.evaluate("usd", after, canonical_symbol="USDJPY")[0])

    def test_weekend_guard_still_wins_over_intraday_schedule(self):
        friday_cutoff = datetime(2026, 8, 28, 20, 30, tzinfo=timezone.utc)
        allowed, reason = self.guard.evaluate("usd", friday_cutoff, canonical_symbol="EURUSD")
        self.assertFalse(allowed)
        self.assertIn("weekend", reason)

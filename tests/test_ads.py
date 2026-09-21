import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_bot.ads import book_ad, publish_booked_ad, revenue_between, run_ad_removal_check
from digest_bot.db import Database
from digest_bot.models import AdStatus
from tests.helpers import FakeTelegramAPI, make_config


class AdsPipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.cfg = make_config()
        self.telegram = FakeTelegramAPI()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_book_publish_and_auto_remove_after_duration(self):
        now = datetime.now(timezone.utc)
        ad_id = book_ad(
            self.db, advertiser="Acme", price=150.0, scheduled_at=now, duration_hours=48, currency="USD"
        )

        msg_id = publish_booked_ad(self.cfg, self.db, self.telegram, ad_id, text="Рекламный пост")
        self.assertEqual(self.telegram.sent[-1]["chat_id"], self.cfg.channel_chat_id)

        # ещё не прошло 48 часов — снимать рано
        too_early = run_ad_removal_check(self.cfg, self.db, self.telegram, now=now + timedelta(hours=10))
        self.assertNotIn(ad_id, too_early.removed)

        # прошло больше 48 часов — пора снимать
        later = now + timedelta(hours=49)
        result = run_ad_removal_check(self.cfg, self.db, self.telegram, now=later)
        self.assertIn(ad_id, result.removed)
        self.assertIn((self.cfg.channel_chat_id, msg_id), self.telegram.deleted)

    def test_revenue_between_sums_by_currency_and_excludes_cancelled(self):
        now = datetime.now(timezone.utc)
        book_ad(self.db, advertiser="A", price=100.0, scheduled_at=now, duration_hours=48, currency="USD")
        book_ad(self.db, advertiser="B", price=50.0, scheduled_at=now, duration_hours=48, currency="USD")
        book_ad(self.db, advertiser="C", price=80.0, scheduled_at=now, duration_hours=48, currency="EUR")

        result = revenue_between(self.db, now - timedelta(hours=1), now + timedelta(hours=1))

        self.assertEqual(result["count"], 3)
        self.assertEqual(result["by_currency"]["USD"], 150.0)
        self.assertEqual(result["by_currency"]["EUR"], 80.0)

    def test_revenue_between_ignores_ads_outside_window(self):
        now = datetime.now(timezone.utc)
        book_ad(self.db, advertiser="Old", price=999.0, scheduled_at=now - timedelta(days=30), duration_hours=48)
        result = revenue_between(self.db, now - timedelta(hours=1), now + timedelta(hours=1))
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()

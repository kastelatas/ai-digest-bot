import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from digest_bot.db import Database
from digest_bot.metrics import collect_metrics_for_recent_posts, collect_subscriber_count
from tests.helpers import FakeTelegramAPI, make_config


class SubscriberCountTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.cfg = make_config()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_collects_and_stores_subscriber_count(self):
        telegram = FakeTelegramAPI(subscriber_count=4321)
        count = collect_subscriber_count(self.cfg, self.db, telegram)
        self.assertEqual(count, 4321)

        from datetime import datetime, timezone

        stats = self.db.channel_stats_since(datetime.now(timezone.utc) - timedelta(hours=1))
        self.assertEqual(stats[-1]["subscribers"], 4321)

    def test_collect_metrics_without_telethon_still_gets_subscribers(self):
        telegram = FakeTelegramAPI(subscriber_count=777)
        result = collect_metrics_for_recent_posts(self.cfg, self.db, telegram, telethon_collector=None)
        self.assertEqual(result["subscribers"], 777)
        self.assertEqual(result["views_collected"], 0)


if __name__ == "__main__":
    unittest.main()

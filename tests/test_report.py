import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_bot.ads import book_ad
from digest_bot.db import Database
from digest_bot.models import Draft, DraftStatus
from digest_bot.report import build_weekly_report
from tests.helpers import make_config, make_item


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.cfg = make_config()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _publish_draft(self, guid, title, published_at, channel_message_id, views=None):
        self.db.add_item(make_item(guid=guid, title=title, link=f"https://example.com/{guid}"))
        draft_id = self.db.create_draft(
            Draft(
                id=None,
                item_guid=guid,
                source_name="Src",
                title=title,
                link=f"https://example.com/{guid}",
                draft_text="text",
                tags=[],
                status=DraftStatus.PENDING,
                created_at=published_at,
            )
        )
        self.db.mark_published(draft_id, channel_message_id, published_at)
        if views is not None:
            self.db.record_metric_snapshot(channel_message_id, views=views, forwards=0, reactions=0)
        return draft_id

    def test_counts_posts_and_subscriber_delta_within_period(self):
        self._publish_draft("g1", "Пост 1", self.now - timedelta(days=1), channel_message_id=101)
        self._publish_draft("g2", "Пост 2", self.now - timedelta(days=10), channel_message_id=102)  # вне окна

        self.db.record_channel_stat(1000)
        # искусственно "состарим" первую запись, чтобы она попала в начало периода
        import sqlite3

        with sqlite3.connect(self.db.path) as conn:
            conn.execute(
                "UPDATE channel_stats SET captured_at = ? WHERE subscribers = 1000",
                ((self.now - timedelta(days=6)).isoformat(),),
            )
        self.db.record_channel_stat(1050)

        report = build_weekly_report(self.cfg, self.db, now=self.now)

        self.assertEqual(report.posts_published, 1)
        self.assertEqual(report.subscribers_start, 1000)
        self.assertEqual(report.subscribers_end, 1050)
        self.assertEqual(report.subscribers_delta, 50)
        self.assertIn("Пост 1", report.text)
        self.assertNotIn("Пост 2", report.text)

    def test_ads_revenue_included(self):
        book_ad(self.db, advertiser="Acme", price=200.0, scheduled_at=self.now, duration_hours=48, currency="USD")
        report = build_weekly_report(self.cfg, self.db, now=self.now)
        self.assertEqual(report.ads_count, 1)
        self.assertEqual(report.ads_revenue_by_currency["USD"], 200.0)
        self.assertIn("200.00 USD", report.text)

    def test_top_posts_sorted_by_views_and_capped_at_five(self):
        for i in range(7):
            self._publish_draft(
                f"g{i}", f"Пост {i}", self.now - timedelta(hours=i), channel_message_id=200 + i, views=i * 10
            )

        report = build_weekly_report(self.cfg, self.db, now=self.now)

        self.assertEqual(len(report.top_posts), 5)
        views_sequence = [p["views"] for p in report.top_posts]
        self.assertEqual(views_sequence, sorted(views_sequence, reverse=True))
        self.assertEqual(report.top_posts[0]["views"], 60)  # пост 6 (i=6 -> 60 просмотров)

    def test_no_data_produces_readable_placeholders_not_crash(self):
        report = build_weekly_report(self.cfg, self.db, now=self.now)
        self.assertEqual(report.posts_published, 0)
        self.assertIsNone(report.subscribers_delta)
        self.assertIn("нет данных", report.text)


if __name__ == "__main__":
    unittest.main()

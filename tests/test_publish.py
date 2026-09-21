import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_bot.db import Database
from digest_bot.models import Draft, DraftStatus
from digest_bot.publish import run_publish
from tests.helpers import FakeTelegramAPI, make_config, make_item


class PublishTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.cfg = make_config(max_posts_per_day=2)
        self.telegram = FakeTelegramAPI()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _approved_draft(self, guid: str, title: str, created_at=None) -> int:
        self.db.add_item(make_item(guid=guid, title=title, link=f"https://example.com/{guid}"))
        draft_id = self.db.create_draft(
            Draft(
                id=None,
                item_guid=guid,
                source_name="Src",
                title=title,
                link=f"https://example.com/{guid}",
                draft_text=f"Текст: {title}",
                tags=[],
                status=DraftStatus.PENDING,
                created_at=created_at or datetime.now(timezone.utc),
            )
        )
        self.db.update_draft_status(draft_id, DraftStatus.APPROVED)
        return draft_id

    def test_publishes_oldest_approved_draft_first(self):
        now = datetime.now(timezone.utc)
        newer = self._approved_draft("g_new", "Новее", created_at=now)
        older = self._approved_draft("g_old", "Старее", created_at=now - timedelta(hours=1))

        result = run_publish(self.cfg, self.db, self.telegram, now=now)

        self.assertTrue(result.published)
        self.assertEqual(result.draft_id, older)
        draft = self.db.get_draft(older)
        self.assertEqual(draft.status, DraftStatus.PUBLISHED)
        self.assertIsNotNone(draft.channel_message_id)

    def test_empty_queue_returns_reason(self):
        result = run_publish(self.cfg, self.db, self.telegram)
        self.assertFalse(result.published)
        self.assertEqual(result.reason, "queue_empty")

    def test_respects_daily_cap(self):
        now = datetime.now(timezone.utc)
        for i in range(3):
            self._approved_draft(f"g{i}", f"Новость {i}", created_at=now - timedelta(minutes=i))

        r1 = run_publish(self.cfg, self.db, self.telegram, now=now)
        r2 = run_publish(self.cfg, self.db, self.telegram, now=now)
        r3 = run_publish(self.cfg, self.db, self.telegram, now=now)  # cfg.max_posts_per_day=2

        self.assertTrue(r1.published)
        self.assertTrue(r2.published)
        self.assertFalse(r3.published)
        self.assertEqual(r3.reason, "daily_cap_reached")

    def test_cap_resets_next_day(self):
        day1 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        for i in range(3):
            self._approved_draft(f"g{i}", f"Новость {i}", created_at=day1)

        run_publish(self.cfg, self.db, self.telegram, now=day1)
        run_publish(self.cfg, self.db, self.telegram, now=day1)
        capped = run_publish(self.cfg, self.db, self.telegram, now=day1)
        self.assertFalse(capped.published)

        next_day = run_publish(self.cfg, self.db, self.telegram, now=day2)
        self.assertTrue(next_day.published)

    def test_telegram_failure_marks_draft_failed_not_stuck_in_queue(self):
        self._approved_draft("g1", "Новость")
        failing_telegram = FakeTelegramAPI(fail_send=True)

        result = run_publish(self.cfg, self.db, failing_telegram)

        self.assertFalse(result.published)
        self.assertTrue(result.reason.startswith("telegram_error"))
        draft = self.db.get_draft(result.draft_id)
        self.assertEqual(draft.status, DraftStatus.FAILED)


if __name__ == "__main__":
    unittest.main()

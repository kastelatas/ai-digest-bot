import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from digest_bot.db import Database
from digest_bot.models import Draft, DraftStatus
from digest_bot.moderation import OFFSET_KEY, process_admin_updates
from tests.helpers import FakeTelegramAPI, make_config, make_item


class ModerationTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.cfg = make_config(allowed_user_ids=[555])
        self.telegram = FakeTelegramAPI()

        self.db.add_item(make_item(guid="g1"))
        self.draft_id = self.db.create_draft(
            Draft(
                id=None,
                item_guid="g1",
                source_name="Src",
                title="T",
                link="https://example.com/1",
                draft_text="Текст черновика",
                tags=[],
                status=DraftStatus.PENDING,
                created_at=datetime.now(timezone.utc),
            )
        )
        self.db.set_admin_message(self.draft_id, chat_id=self.cfg.admin_chat_id, message_id=777)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_approve_by_authorized_user_updates_status_and_message(self):
        self.telegram.queue_callback(
            f"approve:{self.draft_id}", from_user_id=555, chat_id=self.cfg.admin_chat_id,
            message_id=777, text="Текст черновика",
        )

        stats = process_admin_updates(self.cfg, self.db, self.telegram)

        self.assertEqual(stats.approved, 1)
        draft = self.db.get_draft(self.draft_id)
        self.assertEqual(draft.status, DraftStatus.APPROVED)
        self.assertEqual(len(self.telegram.edited_markup), 1)
        self.assertEqual(len(self.telegram.edited_text), 1)
        self.assertIn("Одобрено", self.telegram.edited_text[0]["text"])

    def test_reject_by_authorized_user(self):
        self.telegram.queue_callback(
            f"reject:{self.draft_id}", from_user_id=555, chat_id=self.cfg.admin_chat_id, message_id=777
        )
        stats = process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(stats.rejected, 1)
        self.assertEqual(self.db.get_draft(self.draft_id).status, DraftStatus.REJECTED)

    def test_needs_edit_status(self):
        self.telegram.queue_callback(
            f"edit:{self.draft_id}", from_user_id=555, chat_id=self.cfg.admin_chat_id, message_id=777
        )
        stats = process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(stats.needs_edit, 1)
        self.assertEqual(self.db.get_draft(self.draft_id).status, DraftStatus.NEEDS_EDIT)

    def test_unauthorized_user_is_ignored_and_status_unchanged(self):
        self.telegram.queue_callback(
            f"approve:{self.draft_id}", from_user_id=999, chat_id=self.cfg.admin_chat_id, message_id=777
        )
        stats = process_admin_updates(self.cfg, self.db, self.telegram)

        self.assertEqual(stats.ignored_unauthorized, 1)
        self.assertEqual(stats.approved, 0)
        self.assertEqual(self.db.get_draft(self.draft_id).status, DraftStatus.PENDING)
        self.assertIn("нет прав", self.telegram.answered_callbacks[0]["text"].lower())

    def test_already_processed_draft_is_not_double_processed(self):
        self.db.update_draft_status(self.draft_id, DraftStatus.APPROVED)
        self.telegram.queue_callback(
            f"approve:{self.draft_id}", from_user_id=555, chat_id=self.cfg.admin_chat_id, message_id=777
        )
        stats = process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(stats.approved, 0)
        self.assertIn("Уже обработано", self.telegram.answered_callbacks[0]["text"])

    def test_offset_is_persisted_between_calls(self):
        self.telegram.queue_callback(
            f"approve:{self.draft_id}", from_user_id=555, chat_id=self.cfg.admin_chat_id, message_id=777
        )
        process_admin_updates(self.cfg, self.db, self.telegram)
        saved_offset = self.db.get_state(OFFSET_KEY)
        self.assertIsNotNone(saved_offset)

        # второй проход без новых апдейтов ничего не ломает
        stats2 = process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(stats2.processed, 0)

    def test_unknown_draft_id_is_handled_gracefully(self):
        self.telegram.queue_callback(
            "approve:99999", from_user_id=555, chat_id=self.cfg.admin_chat_id, message_id=1
        )
        stats = process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(stats.approved, 0)
        self.assertIn("не найден", self.telegram.answered_callbacks[0]["text"])


if __name__ == "__main__":
    unittest.main()

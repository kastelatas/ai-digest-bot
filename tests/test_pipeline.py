import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_bot.db import Database
from digest_bot.models import DraftStatus
from digest_bot.pipeline import run_fetch_and_draft
from tests.helpers import FakeSummarizer, FakeTelegramAPI, make_config, make_item


class RunFetchAndDraftTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.cfg = make_config()
        self.telegram = FakeTelegramAPI()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _fetcher(self, items):
        return lambda sources: items

    def test_new_item_becomes_pending_draft_sent_to_admin(self):
        items = [make_item(title="Свежая новость про AI", link="https://example.com/1")]
        summarizer = FakeSummarizer()

        stats = run_fetch_and_draft(self.cfg, self.db, summarizer, self.telegram, fetcher=self._fetcher(items))

        self.assertEqual(stats.fetched, 1)
        self.assertEqual(stats.drafted, 1)
        self.assertEqual(len(self.telegram.sent), 1)
        sent = self.telegram.sent[0]
        self.assertEqual(sent["chat_id"], self.cfg.admin_chat_id)
        self.assertIsNotNone(sent["reply_markup"])

        draft = self.db.get_draft(1)
        self.assertEqual(draft.status, DraftStatus.PENDING)
        self.assertEqual(draft.admin_message_id, sent["message_id"])

    def test_already_seen_guid_is_skipped_on_second_run(self):
        items = [make_item(title="Новость", link="https://example.com/1", guid="same-guid")]
        summarizer = FakeSummarizer()

        run_fetch_and_draft(self.cfg, self.db, summarizer, self.telegram, fetcher=self._fetcher(items))
        stats2 = run_fetch_and_draft(self.cfg, self.db, summarizer, self.telegram, fetcher=self._fetcher(items))

        self.assertEqual(stats2.drafted, 0)
        self.assertEqual(len(self.telegram.sent), 1)  # второй раз в админку ничего не улетело

    def test_fuzzy_duplicate_within_same_run_is_skipped(self):
        items = [
            make_item(title="OpenAI выпустила новую модель для разработчиков", link="https://example.com/1"),
            make_item(title="OpenAI выпустила новую модель разработчикам", link="https://example.com/2"),
        ]
        summarizer = FakeSummarizer()

        stats = run_fetch_and_draft(self.cfg, self.db, summarizer, self.telegram, fetcher=self._fetcher(items))

        self.assertEqual(stats.drafted, 1)
        self.assertEqual(stats.duplicate, 1)

    def test_too_old_item_is_ignored_without_calling_llm(self):
        old_item = make_item(
            title="Старая новость",
            link="https://example.com/old",
            published_at=datetime.now(timezone.utc) - timedelta(hours=100),
        )
        summarizer = FakeSummarizer()

        stats = run_fetch_and_draft(
            self.cfg, self.db, summarizer, self.telegram, fetcher=self._fetcher([old_item])
        )

        self.assertEqual(stats.too_old, 1)
        self.assertEqual(stats.drafted, 0)
        self.assertEqual(summarizer.calls, [])  # LLM вообще не звали — экономим токены

    def test_llm_decline_does_not_create_draft(self):
        item = make_item(title="Недостаточно фактов", link="https://example.com/x")
        summarizer = FakeSummarizer(decline_titles={"Недостаточно фактов"})

        stats = run_fetch_and_draft(self.cfg, self.db, summarizer, self.telegram, fetcher=self._fetcher([item]))

        self.assertEqual(stats.llm_declined, 1)
        self.assertEqual(stats.drafted, 0)
        self.assertEqual(len(self.telegram.sent), 0)

    def test_llm_exception_is_isolated_and_does_not_abort_run(self):
        items = [
            make_item(title="Ломает LLM", link="https://example.com/broken"),
            make_item(title="Нормальная новость", link="https://example.com/ok"),
        ]
        summarizer = FakeSummarizer(fail_titles={"Ломает LLM"})

        stats = run_fetch_and_draft(self.cfg, self.db, summarizer, self.telegram, fetcher=self._fetcher(items))

        self.assertEqual(stats.llm_failed, 1)
        self.assertEqual(stats.drafted, 1)  # вторая новость всё равно обработалась

    def test_runs_without_telegram_client_for_offline_demo(self):
        items = [make_item(title="Демо без телеграма", link="https://example.com/demo")]
        summarizer = FakeSummarizer()

        stats = run_fetch_and_draft(self.cfg, self.db, summarizer, telegram=None, fetcher=self._fetcher(items))

        self.assertEqual(stats.drafted, 1)
        draft = self.db.get_draft(1)
        self.assertIsNone(draft.admin_message_id)


if __name__ == "__main__":
    unittest.main()

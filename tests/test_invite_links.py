import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_bot.db import Database
from digest_bot.invite_links import create_link, record_member_update, revoke_link
from digest_bot.moderation import ALLOWED_UPDATES, JOINS_SINCE_KEY, process_admin_updates
from digest_bot.telegram_api import TelegramAPIError
from tests.helpers import FakeTelegramAPI, make_config


class InviteLinkTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        self.cfg = make_config(channel_chat_id="@test_channel")
        self.telegram = FakeTelegramAPI()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _stats(self):
        return {r["name"]: r for r in self.db.invite_links_with_stats(datetime.utcnow() - timedelta(hours=24))}

    def test_create_link_saves_url_from_telegram_and_uses_default_currency(self):
        link = create_link(self.cfg, self.db, self.telegram, "  seed_habr  ", cost=50, notes="скрин оплаты")
        self.assertEqual(link["name"], "seed_habr")
        self.assertEqual(link["url"], self.telegram.invite_links[0]["invite_link"])
        self.assertEqual(self.telegram.invite_links[0]["chat_id"], "@test_channel")
        self.assertEqual((link["cost"], link["currency"], link["status"]), (50, "USD", "active"))

    def test_join_via_our_link_is_attributed_and_organic_join_is_not(self):
        a = create_link(self.cfg, self.db, self.telegram, "A")
        b = create_link(self.cfg, self.db, self.telegram, "B")
        self.telegram.queue_chat_member(1, joined=True, invite_link=a["url"])
        self.telegram.queue_chat_member(2, joined=True, invite_link=a["url"])
        self.telegram.queue_chat_member(3, joined=True, invite_link=b["url"])
        self.telegram.queue_chat_member(4, joined=True)  # через @username
        self.telegram.queue_chat_member(5, joined=True, invite_link="https://t.me/+someoneelses...")

        stats = process_admin_updates(self.cfg, self.db, self.telegram)

        self.assertEqual(stats.joins, 5)
        by_name = self._stats()
        self.assertEqual((by_name["A"]["joined"], by_name["B"]["joined"]), (2, 1))
        self.assertEqual(self.db.untracked_join_stats()["joined"], 2)

    def test_leave_is_attributed_to_the_link_the_user_joined_by(self):
        a = create_link(self.cfg, self.db, self.telegram, "A")
        self.telegram.queue_chat_member(1, joined=True, invite_link=a["url"])
        self.telegram.queue_chat_member(2, joined=True, invite_link=a["url"])
        self.telegram.queue_chat_member(1, joined=False)  # выход не содержит ссылки

        stats = process_admin_updates(self.cfg, self.db, self.telegram)

        self.assertEqual((stats.joins, stats.leaves), (2, 1))
        self.assertEqual((self._stats()["A"]["joined"], self._stats()["A"]["left_count"]), (2, 1))

    def test_leave_of_unknown_user_is_ignored(self):
        self.telegram.queue_chat_member(99, joined=False)
        process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(self.db.untracked_join_stats(), {"joined": 0, "left_count": 0})

    def test_rejoin_via_another_link_counts_for_the_new_link(self):
        a = create_link(self.cfg, self.db, self.telegram, "A")
        b = create_link(self.cfg, self.db, self.telegram, "B")
        self.telegram.queue_chat_member(1, joined=True, invite_link=a["url"], date=1_780_000_000)
        self.telegram.queue_chat_member(1, joined=False, date=1_780_000_100)
        self.telegram.queue_chat_member(1, joined=True, invite_link=b["url"], date=1_780_000_200)
        self.telegram.queue_chat_member(1, joined=False, date=1_780_000_300)

        process_admin_updates(self.cfg, self.db, self.telegram)

        by_name = self._stats()
        self.assertEqual((by_name["A"]["joined"], by_name["A"]["left_count"]), (1, 1))
        self.assertEqual((by_name["B"]["joined"], by_name["B"]["left_count"]), (1, 1))

    def test_other_chats_and_non_membership_changes_are_ignored(self):
        self.telegram.queue_chat_member(1, joined=True, chat_username="admin_group")
        self.assertIsNone(record_member_update(self.cfg, self.db, {
            "update_id": 50,
            "chat_member": {
                "chat": {"id": -1, "username": "test_channel"}, "date": 1_780_000_000,
                "old_chat_member": {"status": "member", "user": {"id": 7}},
                "new_chat_member": {"status": "administrator", "user": {"id": 7}},  # повышение, а не вход
            },
        }))
        process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(self.db.untracked_join_stats()["joined"], 0)

    def test_numeric_channel_id_is_matched_by_chat_id(self):
        cfg = make_config(channel_chat_id="-100123")
        self.telegram.queue_chat_member(1, joined=True)
        stats = process_admin_updates(cfg, self.db, self.telegram)
        self.assertEqual(stats.joins, 1)

    def test_same_update_is_not_counted_twice(self):
        self.telegram.queue_chat_member(1, joined=True)
        update = self.telegram._pending_updates[0]
        self.assertEqual(record_member_update(self.cfg, self.db, update), "join")
        record_member_update(self.cfg, self.db, update)
        self.assertEqual(self.db.untracked_join_stats()["joined"], 1)

    def test_moderate_requests_chat_member_updates_and_remembers_tracking_start(self):
        self.assertIsNone(self.db.get_state(JOINS_SINCE_KEY))
        process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(self.telegram.last_allowed_updates, ALLOWED_UPDATES)
        self.assertIn("chat_member", ALLOWED_UPDATES)
        first = self.db.get_state(JOINS_SINCE_KEY)
        self.assertIsNotNone(first)
        process_admin_updates(self.cfg, self.db, self.telegram)
        self.assertEqual(self.db.get_state(JOINS_SINCE_KEY), first)  # не перезаписывается

    def test_revoke_marks_link_and_keeps_stats(self):
        a = create_link(self.cfg, self.db, self.telegram, "A")
        self.telegram.queue_chat_member(1, joined=True, invite_link=a["url"])
        process_admin_updates(self.cfg, self.db, self.telegram)

        revoke_link(self.cfg, self.db, self.telegram, a["id"])

        self.assertEqual(self.telegram.revoked_links, [a["url"]])
        self.assertEqual(self.db.get_invite_link(a["id"])["status"], "revoked")
        self.assertEqual(self._stats()["A"]["joined"], 1)

    def test_revoke_without_telegram_only_marks_in_db(self):
        a = create_link(self.cfg, self.db, self.telegram, "A")
        revoke_link(self.cfg, self.db, None, a["id"])
        self.assertEqual(self.telegram.revoked_links, [])
        self.assertEqual(self.db.get_invite_link(a["id"])["status"], "revoked")

    def test_telegram_refusal_to_create_leaves_no_record(self):
        with self.assertRaises(TelegramAPIError):
            create_link(self.cfg, self.db, FakeTelegramAPI(fail_invite=True), "A")
        self.assertEqual(self.db.invite_links_with_stats(datetime.utcnow()), [])

    def test_update_only_touches_editable_fields(self):
        a = create_link(self.cfg, self.db, self.telegram, "A")
        self.db.update_invite_link(a["id"], name="B", cost=12.5, url="hacked", status="revoked")
        row = self.db.get_invite_link(a["id"])
        self.assertEqual((row["name"], row["cost"]), ("B", 12.5))
        self.assertEqual((row["url"], row["status"]), (a["url"], "active"))

    def test_joined_24h_counts_only_recent_joins(self):
        a = create_link(self.cfg, self.db, self.telegram, "A")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.record_join(1, a["id"], a["url"], 1, now - timedelta(hours=2))
        self.db.record_join(2, a["id"], a["url"], 2, now - timedelta(days=3))
        self.assertEqual(self._stats()["A"]["joined"], 2)
        self.assertEqual(self._stats()["A"]["joined_24h"], 1)


if __name__ == "__main__":
    unittest.main()

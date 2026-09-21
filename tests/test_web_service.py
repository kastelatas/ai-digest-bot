import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_bot.db import Database
from digest_bot.models import Draft, DraftStatus
from digest_web.service import MAX_TEXT_LEN, PanelService, ServiceError
from tests.helpers import FakeTelegramAPI, make_config, make_item


class PanelServiceBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "test.db")
        self.cfg = make_config(channel_chat_id="@chan", channel_timezone="UTC")
        self.tg = FakeTelegramAPI()
        self.svc = PanelService(self.cfg, self.db, self.tg)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self._tmp.cleanup()

    def _stat(self, subscribers, when: datetime):
        self.db.record_channel_stat(subscribers)
        with closing(sqlite3.connect(self.db.path)) as conn, conn:
            conn.execute(
                "UPDATE channel_stats SET captured_at = ? WHERE id = (SELECT MAX(id) FROM channel_stats)",
                (when.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),),
            )

    def _draft(self, guid="g1", status=DraftStatus.PENDING, text="Текст", published_at=None, message_id=None,
               admin=None):
        self.db.add_item(make_item(guid=guid, title=guid, link=f"https://x/{guid}"))
        draft_id = self.db.create_draft(
            Draft(id=None, item_guid=guid, source_name="Src", title=f"Заголовок {guid}", link=f"https://x/{guid}",
                  draft_text=text, tags=[], status=status, created_at=self.now)
        )
        if published_at:
            self.db.mark_published(draft_id, message_id, published_at)
        if admin:
            self.db.set_admin_message(draft_id, *admin)
        return draft_id


class SubscribersMetricsTests(PanelServiceBase):
    def test_empty_db_gives_empty_series_and_none_overview(self):
        self.assertEqual(self.svc.subscribers_series(7)["daily"], [])
        ov = self.svc.overview(7)
        self.assertIsNone(ov["subscribers"])
        self.assertIsNone(ov["delta_7d"])
        self.assertEqual(ov["posts_total"], 0)

    def test_gained_and_lost_are_derived_from_consecutive_snapshots(self):
        d1 = self.now - timedelta(days=2)
        self._stat(100, d1.replace(hour=8))
        self._stat(110, d1.replace(hour=20))   # +10
        self._stat(105, self.now - timedelta(days=1))  # -5
        self._stat(105, self.now)              # 0

        series = self.svc.subscribers_series(7)
        by_date = {r["date"]: r for r in series["daily"]}
        first = by_date[d1.date().isoformat()]
        self.assertEqual((first["gained"], first["lost"], first["subscribers"]), (10, 0, 110))
        second = by_date[(self.now - timedelta(days=1)).date().isoformat()]
        self.assertEqual((second["gained"], second["lost"], second["net"]), (0, 5, -5))
        self.assertEqual(len(series["points"]), 4)

        ov = self.svc.overview(7)
        self.assertEqual((ov["subscribers"], ov["gained"], ov["lost"]), (105, 10, 5))

    def test_days_without_snapshots_carry_last_value_with_zero_change(self):
        self._stat(50, self.now - timedelta(days=3))
        daily = self.svc.subscribers_series(7)["daily"]
        self.assertEqual(daily[-1]["subscribers"], 50)
        self.assertEqual((daily[-1]["gained"], daily[-1]["lost"], daily[-1]["snapshots"]), (0, 0, 0))
        self.assertEqual(daily[-1]["date"], self.now.date().isoformat())

    def test_window_starts_from_previous_value_and_ignores_older_days_in_output(self):
        self._stat(10, self.now - timedelta(days=40))
        self._stat(30, self.now - timedelta(days=1))  # +20 внутри окна относительно замера до окна
        series = self.svc.subscribers_series(7)
        self.assertEqual(len(series["daily"]), 7)
        self.assertEqual(series["daily"][0]["subscribers"], 10)  # значение «до окна» тянется вперёд
        self.assertEqual(self.svc.overview(7)["gained"], 20)

    def test_posts_daily_counts_published_per_local_day(self):
        self._draft("a", DraftStatus.PENDING, published_at=self.now, message_id=1)
        self._draft("b", DraftStatus.PENDING, published_at=self.now - timedelta(hours=1), message_id=2)
        self._draft("c", DraftStatus.PENDING)  # не опубликован
        daily = self.svc.posts_daily(3)
        self.assertEqual(len(daily), 3)
        self.assertGreaterEqual(sum(d["published"] for d in daily), 2)
        self.assertEqual(self.svc.overview(3)["posts_by_status"]["pending"], 1)


class PostReadTests(PanelServiceBase):
    def test_list_filters_search_and_paginates(self):
        for i in range(5):
            self._draft(f"g{i}", text=f"про робота {i}" if i % 2 == 0 else "про погоду")
        self._draft("gx", DraftStatus.REJECTED)

        everything = self.svc.list_posts(None, None, 25, 0)
        self.assertEqual(everything["total"], 6)
        self.assertGreater(everything["items"][0]["id"], everything["items"][-1]["id"])  # новые первыми

        self.assertEqual(self.svc.list_posts("rejected", None, 25, 0)["total"], 1)
        self.assertEqual(self.svc.list_posts(None, "робота", 25, 0)["total"], 3)
        page = self.svc.list_posts(None, None, 2, 2)
        self.assertEqual((len(page["items"]), page["total"]), (2, 6))

    def test_unknown_status_filter_is_rejected(self):
        with self.assertRaises(ServiceError) as ctx:
            self.svc.list_posts("bogus", None, 25, 0)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_published_post_has_channel_url_and_metrics(self):
        pid = self._draft("p", published_at=self.now, message_id=77)
        self.db.record_metric_snapshot(77, views=120, forwards=3, reactions=5)
        item = self.svc.list_posts(None, None, 25, 0)["items"][0]
        self.assertEqual(item["id"], pid)
        self.assertEqual(item["channel_url"], "https://t.me/chan/77")
        self.assertEqual((item["views"], item["forwards"], item["reactions"]), (120, 3, 5))


class PostCreateTests(PanelServiceBase):
    def test_create_draft_and_queue_do_not_touch_telegram(self):
        draft = self.svc.create_post("<b>Заголовок</b>\n\nТекст", "draft")
        queued = self.svc.create_post("Второй пост", "queue")
        self.assertEqual((draft["status"], queued["status"]), ("pending", "approved"))
        self.assertEqual(draft["title"], "Заголовок")  # теги вырезаны из заголовка
        self.assertEqual(self.tg.sent, [])
        self.assertEqual([d.id for d in self.db.approved_drafts()], [queued["id"]])  # подхватит cron publish

    def test_publish_now_sends_to_channel_and_marks_published(self):
        post = self.svc.create_post("Срочно в канал", "publish")
        self.assertEqual(post["status"], "published")
        self.assertEqual(self.tg.sent[-1]["chat_id"], "@chan")
        self.assertEqual(post["channel_message_id"], self.tg.sent[-1]["message_id"])

    def test_publish_failure_keeps_draft_and_reports_502(self):
        self.svc.telegram = FakeTelegramAPI(fail_send=True)
        with self.assertRaises(ServiceError) as ctx:
            self.svc.create_post("Не уйдёт", "publish")
        self.assertEqual(ctx.exception.status_code, 502)
        posts = self.svc.list_posts(None, None, 25, 0)["items"]
        self.assertEqual([p["status"] for p in posts], ["pending"])

    def test_validation(self):
        for text, mode in [("   ", "draft"), ("x" * (MAX_TEXT_LEN + 1), "draft"), ("ok", "bogus")]:
            with self.assertRaises(ServiceError) as ctx:
                self.svc.create_post(text, mode)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_publish_without_token_is_503_and_creates_nothing(self):
        svc = PanelService(self.cfg, self.db, None)
        with self.assertRaises(ServiceError) as ctx:
            svc.create_post("текст", "publish")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(self.db.list_drafts()[1], 0)


class PostUpdateTests(PanelServiceBase):
    def test_edit_draft_text_only_touches_db(self):
        pid = self._draft("g", text="старый")
        post = self.svc.update_post(pid, "новый", None)
        self.assertEqual(post["text"], "новый")
        self.assertEqual(self.tg.edited_text, [])

    def test_edit_published_updates_channel_message_with_html(self):
        pid = self._draft("g", text="старый", published_at=self.now, message_id=555)
        self.svc.update_post(pid, "<b>новый</b>", None)
        self.assertEqual(self.tg.edited_text[-1]["message_id"], 555)
        self.assertEqual(self.tg.edited_text[-1]["chat_id"], "@chan")
        self.assertEqual(self.tg.edited_text[-1]["parse_mode"], "HTML")
        self.assertEqual(self.db.get_draft(pid).draft_text, "<b>новый</b>")

    def test_telegram_edit_error_leaves_db_unchanged(self):
        pid = self._draft("g", text="старый", published_at=self.now, message_id=1)
        self.svc.telegram = FakeTelegramAPI(edit_error="Bad Request: can't parse entities")
        with self.assertRaises(ServiceError) as ctx:
            self.svc.update_post(pid, "<b>сломан", None)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(self.db.get_draft(pid).draft_text, "старый")

    def test_message_not_modified_is_not_an_error(self):
        pid = self._draft("g", text="старый", published_at=self.now, message_id=1)
        self.svc.telegram = FakeTelegramAPI(edit_error="Bad Request: message is not modified")
        self.svc.update_post(pid, "почти старый", None)
        self.assertEqual(self.db.get_draft(pid).draft_text, "почти старый")

    def test_status_transitions(self):
        pid = self._draft("g")
        self.assertEqual(self.svc.update_post(pid, None, "approved")["status"], "approved")
        self.assertEqual(self.svc.update_post(pid, None, "rejected")["status"], "rejected")
        for bad in ("published", "failed"):
            with self.assertRaises(ServiceError) as ctx:
                self.svc.update_post(pid, None, bad)
            self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(ServiceError):
            self.svc.update_post(pid, None, "nonsense")

    def test_published_post_status_is_frozen(self):
        pid = self._draft("g", published_at=self.now, message_id=1)
        with self.assertRaises(ServiceError) as ctx:
            self.svc.update_post(pid, None, "rejected")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_missing_post_is_404(self):
        with self.assertRaises(ServiceError) as ctx:
            self.svc.update_post(999, "x", None)
        self.assertEqual(ctx.exception.status_code, 404)


class PostDeleteTests(PanelServiceBase):
    def test_delete_draft_removes_row_and_admin_message_but_keeps_item(self):
        pid = self._draft("g", admin=(111, 42))
        self.svc.delete_post(pid)
        self.assertIsNone(self.db.get_draft(pid))
        self.assertIn((111, 42), self.tg.deleted)
        self.assertTrue(self.db.item_exists("g"))  # новость не будет предложена заново

    def test_delete_published_removes_channel_message_first(self):
        pid = self._draft("g", published_at=self.now, message_id=900)
        self.svc.delete_post(pid)
        self.assertIn(("@chan", 900), self.tg.deleted)
        self.assertIsNone(self.db.get_draft(pid))

    def test_channel_delete_failure_keeps_record_unless_forced(self):
        pid = self._draft("g", published_at=self.now, message_id=900)
        self.svc.telegram = FakeTelegramAPI(fail_delete=True)
        with self.assertRaises(ServiceError) as ctx:
            self.svc.delete_post(pid)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIsNotNone(self.db.get_draft(pid))
        self.svc.delete_post(pid, force=True)
        self.assertIsNone(self.db.get_draft(pid))


if __name__ == "__main__":
    unittest.main()

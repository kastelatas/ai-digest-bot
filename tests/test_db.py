import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_bot.db import Database
from digest_bot.models import AdBooking, AdStatus, Draft, DraftStatus
from tests.helpers import make_item


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")

    def tearDown(self):
        self._tmpdir.cleanup()


class ItemsTests(DatabaseTestCase):
    def test_add_item_and_exists(self):
        item = make_item(guid="g1")
        self.assertFalse(self.db.item_exists("g1"))
        self.db.add_item(item)
        self.assertTrue(self.db.item_exists("g1"))

    def test_add_item_twice_is_idempotent(self):
        item = make_item(guid="g1")
        self.db.add_item(item)
        self.db.add_item(item)  # не должно упасть на UNIQUE constraint
        self.assertTrue(self.db.item_exists("g1"))

    def test_recent_titles_respects_window_and_status(self):
        self.db.add_item(make_item(title="A", guid="a"))
        self.db.mark_item_status("a", "ignored")
        self.db.add_item(make_item(title="B", guid="b"))
        titles = self.db.recent_titles(hours=24)
        self.assertIn("B", titles)
        self.assertNotIn("A", titles)  # ignored исключается


class DraftsTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        # drafts.item_guid ссылается на items(guid) — как и в реальном пайплайне
        # (pipeline.py всегда делает db.add_item раньше db.create_draft),
        # поэтому для каждого черновика в тестах сперва заводим item.
        self.db.add_item(make_item(guid="g1", title="Item G1"))
        self.db.add_item(make_item(guid="g2", title="Item G2", link="https://example.com/2"))

    def _draft(self, **overrides) -> Draft:
        defaults = dict(
            id=None,
            item_guid="g1",
            source_name="Src",
            title="T",
            link="https://example.com/1",
            draft_text="text",
            tags=["ai"],
            status=DraftStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        return Draft(**defaults)

    def test_create_and_get_draft_roundtrip(self):
        draft_id = self.db.create_draft(self._draft())
        draft = self.db.get_draft(draft_id)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.title, "T")
        self.assertEqual(draft.tags, ["ai"])
        self.assertEqual(draft.status, DraftStatus.PENDING)

    def test_set_and_lookup_admin_message(self):
        draft_id = self.db.create_draft(self._draft())
        self.db.set_admin_message(draft_id, chat_id=111, message_id=999)
        found = self.db.get_draft_by_admin_message(111, 999)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, draft_id)

    def test_approved_drafts_ordered_fifo(self):
        id1 = self.db.create_draft(self._draft(title="First"))
        id2 = self.db.create_draft(self._draft(title="Second", item_guid="g2"))
        self.db.update_draft_status(id2, DraftStatus.APPROVED)
        self.db.update_draft_status(id1, DraftStatus.APPROVED)
        queue = self.db.approved_drafts()
        self.assertEqual([d.id for d in queue], [id1, id2])  # по created_at, а не по порядку approve

    def test_mark_published_updates_status_and_message_id(self):
        draft_id = self.db.create_draft(self._draft())
        now = datetime.now(timezone.utc)
        self.db.mark_published(draft_id, channel_message_id=42, published_at=now)
        draft = self.db.get_draft(draft_id)
        self.assertEqual(draft.status, DraftStatus.PUBLISHED)
        self.assertEqual(draft.channel_message_id, 42)


class KVStateTests(DatabaseTestCase):
    def test_get_default_when_missing(self):
        self.assertIsNone(self.db.get_state("missing"))
        self.assertEqual(self.db.get_state("missing", "default"), "default")

    def test_set_then_get(self):
        self.db.set_state("offset", "123")
        self.assertEqual(self.db.get_state("offset"), "123")

    def test_set_overwrites(self):
        self.db.set_state("offset", "1")
        self.db.set_state("offset", "2")
        self.assertEqual(self.db.get_state("offset"), "2")


class AdsTests(DatabaseTestCase):
    def _ad(self, **overrides) -> AdBooking:
        defaults = dict(
            id=None,
            advertiser="Acme",
            contact="@acme",
            price=100.0,
            currency="USD",
            scheduled_at=datetime.now(timezone.utc),
            duration_hours=48,
            status=AdStatus.BOOKED,
        )
        defaults.update(overrides)
        return AdBooking(**defaults)

    def test_add_and_retrieve_by_range(self):
        now = datetime.now(timezone.utc)
        self.db.add_ad(self._ad(scheduled_at=now))
        ads = self.db.ads_between(now - timedelta(hours=1), now + timedelta(hours=1))
        self.assertEqual(len(ads), 1)
        self.assertEqual(ads[0].advertiser, "Acme")

    def test_due_for_removal_only_after_duration_elapsed(self):
        now = datetime.now(timezone.utc)
        published_recent = self._ad(scheduled_at=now - timedelta(hours=1), duration_hours=48)
        published_old = self._ad(scheduled_at=now - timedelta(hours=50), duration_hours=48)
        id_recent = self.db.add_ad(published_recent)
        id_old = self.db.add_ad(published_old)
        self.db.set_ad_channel_message(id_recent, 1)
        self.db.set_ad_channel_message(id_old, 2)

        due = self.db.ads_due_for_removal(now)
        due_ids = {ad.id for ad in due}
        self.assertIn(id_old, due_ids)
        self.assertNotIn(id_recent, due_ids)

    def test_mark_removed_excludes_from_due(self):
        now = datetime.now(timezone.utc)
        ad_id = self.db.add_ad(self._ad(scheduled_at=now - timedelta(hours=50), duration_hours=48))
        self.db.set_ad_channel_message(ad_id, 1)
        self.db.mark_ad_removed(ad_id)
        due = self.db.ads_due_for_removal(now)
        self.assertNotIn(ad_id, {ad.id for ad in due})


class ChannelStatsTests(DatabaseTestCase):
    def test_record_and_read_back(self):
        self.db.record_channel_stat(1000)
        self.db.record_channel_stat(1010)
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        stats = self.db.channel_stats_since(since)
        self.assertEqual([s["subscribers"] for s in stats], [1000, 1010])


if __name__ == "__main__":
    unittest.main()

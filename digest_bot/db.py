"""Слой хранения на SQLite (только stdlib).

Пайплайн работает как набор независимых команд (fetch / moderate / publish /
collect-metrics / weekly-report), которые обычно запускает cron — поэтому
соединения короткоживущие, а база в режиме WAL, чтобы разные процессы не
блокировали друг друга.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .models import AdBooking, AdStatus, Draft, DraftStatus, FeedItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT UNIQUE NOT NULL,
    source_name TEXT NOT NULL,
    source_category TEXT NOT NULL,
    source_lang TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    summary TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_guid TEXT NOT NULL REFERENCES items(guid),
    source_name TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    draft_text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    admin_chat_id INTEGER,
    admin_message_id INTEGER,
    channel_message_id INTEGER,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_message_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    views INTEGER,
    forwards INTEGER,
    reactions INTEGER
);

CREATE TABLE IF NOT EXISTS channel_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    subscribers INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    advertiser TEXT NOT NULL,
    contact TEXT,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    duration_hours INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked',
    erid TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    channel_message_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_ads_status ON ads(status);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Database:
    def __init__(self, path: str | Path = "data/digest.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ---------- items / dedup ----------

    def item_exists(self, guid: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM items WHERE guid = ?", (guid,)).fetchone()
            return row is not None

    def add_item(self, item: FeedItem) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO items
                   (guid, source_name, source_category, source_lang, title, link,
                    summary, published_at, fetched_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
                (
                    item.guid,
                    item.source_name,
                    item.source_category,
                    item.source_lang,
                    item.title,
                    item.link,
                    item.summary,
                    _iso(item.published_at),
                    datetime.utcnow().isoformat(),
                ),
            )
            return cur.lastrowid

    def mark_item_status(self, guid: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE items SET status = ? WHERE guid = ?", (status, guid))

    def recent_titles(self, hours: int) -> list[str]:
        """Заголовки за последние N часов — для fuzzy-дедупа по смыслу."""
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT title FROM items WHERE fetched_at >= ? AND status != 'ignored'",
                (since,),
            ).fetchall()
            return [r["title"] for r in rows]

    # ---------- drafts ----------

    def create_draft(self, draft: Draft) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO drafts
                   (item_guid, source_name, title, link, draft_text, tags, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft.item_guid,
                    draft.source_name,
                    draft.title,
                    draft.link,
                    draft.draft_text,
                    json.dumps(draft.tags, ensure_ascii=False),
                    draft.status.value,
                    _iso(draft.created_at),
                ),
            )
            return cur.lastrowid

    def set_admin_message(self, draft_id: int, chat_id: int, message_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE drafts SET admin_chat_id = ?, admin_message_id = ? WHERE id = ?",
                (chat_id, message_id, draft_id),
            )

    def get_draft(self, draft_id: int) -> Draft | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            return self._row_to_draft(row) if row else None

    def get_draft_by_admin_message(self, chat_id: int, message_id: int) -> Draft | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE admin_chat_id = ? AND admin_message_id = ?",
                (chat_id, message_id),
            ).fetchone()
            return self._row_to_draft(row) if row else None

    def update_draft_status(self, draft_id: int, status: DraftStatus) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE drafts SET status = ? WHERE id = ?", (status.value, draft_id))

    def approved_drafts(self) -> list[Draft]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM drafts WHERE status = ? ORDER BY created_at ASC",
                (DraftStatus.APPROVED.value,),
            ).fetchall()
            return [self._row_to_draft(r) for r in rows]

    def mark_published(self, draft_id: int, channel_message_id: int, published_at: datetime) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE drafts SET status = ?, channel_message_id = ?, published_at = ?
                   WHERE id = ?""",
                (DraftStatus.PUBLISHED.value, channel_message_id, _iso(published_at), draft_id),
            )

    def posts_published_since(self, since: datetime) -> list[Draft]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM drafts WHERE status = ? AND published_at >= ?",
                (DraftStatus.PUBLISHED.value, _iso(since)),
            ).fetchall()
            return [self._row_to_draft(r) for r in rows]

    def all_published(self) -> list[Draft]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM drafts WHERE status = ? ORDER BY published_at DESC",
                (DraftStatus.PUBLISHED.value,),
            ).fetchall()
            return [self._row_to_draft(r) for r in rows]

    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> Draft:
        return Draft(
            id=row["id"],
            item_guid=row["item_guid"],
            source_name=row["source_name"],
            title=row["title"],
            link=row["link"],
            draft_text=row["draft_text"],
            tags=json.loads(row["tags"]),
            status=DraftStatus(row["status"]),
            created_at=_parse_dt(row["created_at"]),
            admin_message_id=row["admin_message_id"],
            channel_message_id=row["channel_message_id"],
            published_at=_parse_dt(row["published_at"]),
        )

    # ---------- metrics ----------

    def record_metric_snapshot(
        self, channel_message_id: int, views: int | None, forwards: int | None, reactions: int | None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO metrics_snapshots
                   (channel_message_id, captured_at, views, forwards, reactions)
                   VALUES (?, ?, ?, ?, ?)""",
                (channel_message_id, datetime.utcnow().isoformat(), views, forwards, reactions),
            )

    def latest_metric(self, channel_message_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM metrics_snapshots WHERE channel_message_id = ?
                   ORDER BY captured_at DESC LIMIT 1""",
                (channel_message_id,),
            ).fetchone()
            return dict(row) if row else None

    def record_channel_stat(self, subscribers: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO channel_stats (captured_at, subscribers) VALUES (?, ?)",
                (datetime.utcnow().isoformat(), subscribers),
            )

    def channel_stats_since(self, since: datetime) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM channel_stats WHERE captured_at >= ? ORDER BY captured_at ASC",
                (_iso(since),),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- ads ----------

    def add_ad(self, ad: AdBooking) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO ads
                   (advertiser, contact, price, currency, scheduled_at, duration_hours,
                    status, erid, notes, channel_message_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ad.advertiser,
                    ad.contact,
                    ad.price,
                    ad.currency,
                    _iso(ad.scheduled_at),
                    ad.duration_hours,
                    ad.status.value,
                    ad.erid,
                    ad.notes,
                    ad.channel_message_id,
                    _iso(ad.created_at),
                ),
            )
            return cur.lastrowid

    def set_ad_channel_message(self, ad_id: int, channel_message_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE ads SET channel_message_id = ?, status = ? WHERE id = ?",
                (channel_message_id, AdStatus.PUBLISHED.value, ad_id),
            )

    def ads_due_for_removal(self, now: datetime) -> list[AdBooking]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ads WHERE status = ?", (AdStatus.PUBLISHED.value,)
            ).fetchall()
            due = []
            for r in rows:
                scheduled = _parse_dt(r["scheduled_at"])
                if scheduled and now >= scheduled + timedelta(hours=r["duration_hours"]):
                    due.append(self._row_to_ad(r))
            return due

    def mark_ad_removed(self, ad_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE ads SET status = ? WHERE id = ?", (AdStatus.REMOVED.value, ad_id))

    def ads_between(self, start: datetime, end: datetime) -> list[AdBooking]:
        # Верхняя граница включительно: end обычно = "сейчас" (момент генерации
        # отчёта), и бронирование, созданное в этот же момент, не должно
        # выпадать из окна из-за строгого сравнения.
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ads WHERE scheduled_at >= ? AND scheduled_at <= ?",
                (_iso(start), _iso(end)),
            ).fetchall()
            return [self._row_to_ad(r) for r in rows]

    # ---------- kv state (offset апдейтов Telegram, дата последнего отчёта, …) ----------

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO kv_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    @staticmethod
    def _row_to_ad(row: sqlite3.Row) -> AdBooking:
        return AdBooking(
            id=row["id"],
            advertiser=row["advertiser"],
            contact=row["contact"] or "",
            price=row["price"],
            currency=row["currency"],
            scheduled_at=_parse_dt(row["scheduled_at"]),
            duration_hours=row["duration_hours"],
            status=AdStatus(row["status"]),
            erid=row["erid"] or "",
            notes=row["notes"] or "",
            channel_message_id=row["channel_message_id"],
            created_at=_parse_dt(row["created_at"]),
        )

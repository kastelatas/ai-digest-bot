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

CREATE TABLE IF NOT EXISTS invite_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    cost REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    ad_text TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

-- Каждое вступление в канал, о котором сообщил Telegram (update chat_member).
-- link_id пуст, если человек пришёл не по нашей ссылке (поиск, @username, чужая ссылка).
CREATE TABLE IF NOT EXISTS link_joins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id INTEGER UNIQUE,
    link_id INTEGER REFERENCES invite_links(id),
    invite_link TEXT,
    user_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL,
    left_at TEXT
);

CREATE TABLE IF NOT EXISTS kv_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_link_joins_link ON link_joins(link_id);
CREATE INDEX IF NOT EXISTS idx_link_joins_user ON link_joins(user_id, left_at);
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

    def list_drafts(
        self, status: str | None = None, search: str | None = None, limit: int = 25, offset: int = 0
    ) -> tuple[list[Draft], int]:
        """Страница черновиков (новые первыми) и общее число под фильтр — для веб-панели."""
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if search:
            like = f"%{search}%"
            where.append("(title LIKE ? OR draft_text LIKE ?)")
            params += [like, like]
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM drafts{clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM drafts{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return [self._row_to_draft(r) for r in rows], total

    def draft_counts(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM drafts GROUP BY status").fetchall()
            return {r["status"]: r["n"] for r in rows}

    def update_draft_text(self, draft_id: int, text: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE drafts SET draft_text = ? WHERE id = ?", (text, draft_id))

    def delete_draft(self, draft_id: int) -> None:
        # строка в items остаётся — новость не будет предложена повторно
        with self._conn() as conn:
            conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))

    def mark_item_drafted(self, guid: str) -> None:
        self.mark_item_status(guid, "drafted")

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
            admin_chat_id=row["admin_chat_id"],
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

    def all_channel_stats(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM channel_stats ORDER BY captured_at ASC, id ASC").fetchall()
            return [dict(r) for r in rows]

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

    def all_ads(self) -> list[AdBooking]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM ads ORDER BY id ASC").fetchall()
            return [self._row_to_ad(r) for r in rows]

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

    # ---------- ссылки-приглашения и вступления по ним ----------

    _LINK_EDITABLE = {"name", "cost", "currency", "ad_text", "notes"}

    def add_invite_link(
        self, name: str, url: str, cost: float | None = None, currency: str = "USD",
        ad_text: str = "", notes: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO invite_links (name, url, cost, currency, ad_text, notes, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
                (name, url, cost, currency, ad_text, notes, datetime.utcnow().isoformat()),
            )
            return cur.lastrowid

    def get_invite_link(self, link_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM invite_links WHERE id = ?", (link_id,)).fetchone()
            return dict(row) if row else None

    def get_invite_link_by_url(self, url: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM invite_links WHERE url = ?", (url,)).fetchone()
            return dict(row) if row else None

    def update_invite_link(self, link_id: int, **fields) -> None:
        fields = {k: v for k, v in fields.items() if k in self._LINK_EDITABLE}
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as conn:
            conn.execute(f"UPDATE invite_links SET {assignments} WHERE id = ?", [*fields.values(), link_id])

    def mark_invite_link_revoked(self, link_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE invite_links SET status = 'revoked', revoked_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), link_id),
            )

    def invite_links_with_stats(self, since_24h: datetime) -> list[dict]:
        """Все ссылки (новые первыми) с числом вступивших / ушедших и вступлений за 24 часа."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT l.*,
                          COUNT(j.id) AS joined,
                          COALESCE(SUM(CASE WHEN j.left_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS left_count,
                          COALESCE(SUM(CASE WHEN j.joined_at >= ? THEN 1 ELSE 0 END), 0) AS joined_24h,
                          MAX(j.joined_at) AS last_join_at
                   FROM invite_links l LEFT JOIN link_joins j ON j.link_id = l.id
                   GROUP BY l.id ORDER BY l.id DESC""",
                (since_24h.isoformat(),),
            ).fetchall()
            return [dict(r) for r in rows]

    def untracked_join_stats(self) -> dict:
        """Вступления не по нашим ссылкам: поиск, @username, чужие ссылки."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS joined,
                          COALESCE(SUM(CASE WHEN left_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS left_count
                   FROM link_joins WHERE link_id IS NULL"""
            ).fetchone()
            return dict(row)

    def join_rows(self, link_id: int | None) -> list[dict]:
        """Вступления одной ссылки (link_id=None — все «не по нашим ссылкам») для графика по дням."""
        with self._conn() as conn:
            if link_id is None:
                rows = conn.execute("SELECT joined_at, left_at FROM link_joins WHERE link_id IS NULL").fetchall()
            else:
                rows = conn.execute(
                    "SELECT joined_at, left_at FROM link_joins WHERE link_id = ?", (link_id,)
                ).fetchall()
            return [dict(r) for r in rows]

    def record_join(
        self, update_id: int | None, link_id: int | None, invite_link: str | None,
        user_id: int, joined_at: datetime,
    ) -> bool:
        """Идемпотентно по update_id: повторная обработка того же апдейта ничего не дублирует."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO link_joins (update_id, link_id, invite_link, user_id, joined_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (update_id, link_id, invite_link, user_id, joined_at.isoformat()),
            )
            return cur.rowcount > 0

    def record_leave(self, user_id: int, left_at: datetime) -> bool:
        """Отмечает выход на последнем ещё открытом вступлении этого человека (если мы его видели)."""
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE link_joins SET left_at = ?
                   WHERE id = (SELECT id FROM link_joins WHERE user_id = ? AND left_at IS NULL
                               ORDER BY joined_at DESC, id DESC LIMIT 1)""",
                (left_at.isoformat(), user_id),
            )
            return cur.rowcount > 0

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

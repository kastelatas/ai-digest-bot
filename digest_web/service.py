"""Логика веб-панели поверх БД и Telegram: метрики канала и CRUD постов.

Без FastAPI — чтобы тестировать обычным unittest и не тянуть веб-зависимости
в тесты пайплайна. HTTP-слой — digest_web/app.py.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from digest_bot.config import Config
from digest_bot.db import Database
from digest_bot.models import Draft, DraftStatus, FeedItem
from digest_bot.telegram_api import TelegramAPI, TelegramAPIError

logger = logging.getLogger(__name__)

MAX_TEXT_LEN = 4096  # лимит Telegram на длину сообщения
# статусы, которые можно выставлять руками; published/failed выставляет только пайплайн
SETTABLE_STATUSES = {DraftStatus.PENDING, DraftStatus.APPROVED, DraftStatus.NEEDS_EDIT, DraftStatus.REJECTED}
CREATE_MODES = {"draft", "queue", "publish"}
_TAG_RE = re.compile(r"<[^>]+>")


class ServiceError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _local_tz(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Часовой пояс %r не найден — использую UTC", name)
        return timezone.utc


def _aware_utc(dt: datetime) -> datetime:
    # в БД naive-время — это UTC (datetime.utcnow())
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return _aware_utc(dt).isoformat().replace("+00:00", "Z")


class PanelService:
    def __init__(self, cfg: Config, db: Database, telegram: TelegramAPI | None):
        self.cfg = cfg
        self.db = db
        self.telegram = telegram
        self.tz = _local_tz(cfg.channel_timezone)

    # ---------- метрики ----------

    def _today(self) -> date:
        return datetime.now(timezone.utc).astimezone(self.tz).date()

    def _local_date(self, dt: datetime) -> date:
        return _aware_utc(dt).astimezone(self.tz).date()

    def _window(self, days: int) -> tuple[date, date]:
        today = self._today()
        return today - timedelta(days=days - 1), today

    def subscribers_series(self, days: int = 30) -> dict:
        """Подписчики: сырые замеры и по дням.

        Bot API отдаёт только ТЕКУЩЕЕ число подписчиков, а не события. Поэтому
        «пришло/ушло» считается по разнице между соседними замерами: рост между
        замерами — подписки, падение — отписки. Подписка и отписка внутри одного
        интервала между замерами взаимно вычитаются (чем чаще замеры, тем точнее).
        """
        first_day, last_day = self._window(days)
        snapshots = [
            (_aware_utc(datetime.fromisoformat(r["captured_at"])), r["subscribers"])
            for r in self.db.all_channel_stats()
        ]

        per_day: dict[date, dict] = {}
        prev: int | None = None
        for taken_at, subs in snapshots:
            day = self._local_date(taken_at)
            row = per_day.setdefault(day, {"subscribers": subs, "gained": 0, "lost": 0, "snapshots": 0})
            if prev is not None:
                delta = subs - prev
                row["gained"] += max(delta, 0)
                row["lost"] += max(-delta, 0)
            row["subscribers"] = subs
            row["snapshots"] += 1
            prev = subs

        daily: list[dict] = []
        # последнее известное значение до окна — им заполняются дни без замеров
        carried = next((per_day[d]["subscribers"] for d in sorted(per_day, reverse=True) if d < first_day), None)
        in_window = sorted(d for d in per_day if d >= first_day)
        if carried is not None or in_window:
            d = first_day if carried is not None else in_window[0]
            while d <= last_day:
                row = per_day.get(d)
                if row:
                    carried = row["subscribers"]
                    daily.append({"date": d.isoformat(), **row, "net": row["gained"] - row["lost"]})
                else:
                    daily.append({"date": d.isoformat(), "subscribers": carried, "gained": 0, "lost": 0,
                                  "snapshots": 0, "net": 0})
                d += timedelta(days=1)

        cutoff = datetime.combine(first_day, datetime.min.time(), tzinfo=self.tz).astimezone(timezone.utc)
        points = [{"t": _iso_z(t), "subscribers": s} for t, s in snapshots if t >= cutoff][-1000:]
        return {"days": days, "daily": daily, "points": points}

    def posts_daily(self, days: int = 30) -> list[dict]:
        first_day, last_day = self._window(days)
        counts: dict[date, int] = {}
        for draft in self.db.all_published():
            if draft.published_at:
                day = self._local_date(draft.published_at)
                counts[day] = counts.get(day, 0) + 1
        out, d = [], first_day
        while d <= last_day:
            out.append({"date": d.isoformat(), "published": counts.get(d, 0)})
            d += timedelta(days=1)
        return out

    def overview(self, days: int = 30) -> dict:
        series = self.subscribers_series(days)
        stats = self.db.all_channel_stats()
        current = stats[-1]["subscribers"] if stats else None
        last_at = _iso_z(datetime.fromisoformat(stats[-1]["captured_at"])) if stats else None

        def delta_since(delta_days: int) -> int | None:
            if current is None or len(stats) < 2:
                return None
            border = datetime.now(timezone.utc) - timedelta(days=delta_days)
            base = stats[0]["subscribers"]
            for r in stats:
                if _aware_utc(datetime.fromisoformat(r["captured_at"])) <= border:
                    base = r["subscribers"]
            return current - base

        counts = self.db.draft_counts()
        posts_in_period = sum(p["published"] for p in self.posts_daily(days))
        return {
            "days": days,
            "subscribers": current,
            "last_snapshot_at": last_at,
            "delta_1d": delta_since(1),
            "delta_7d": delta_since(7),
            "delta_30d": delta_since(30),
            "gained": sum(r["gained"] for r in series["daily"]),
            "lost": sum(r["lost"] for r in series["daily"]),
            "posts_published_period": posts_in_period,
            "posts_by_status": counts,
            "posts_total": sum(counts.values()),
            "timezone": self.cfg.channel_timezone,
        }

    # ---------- посты: чтение ----------

    def _channel_url(self, message_id: int | None) -> str | None:
        chat = self.cfg.channel_chat_id
        if message_id and chat.startswith("@"):
            return f"https://t.me/{chat[1:]}/{message_id}"
        return None

    def _post_dict(self, d: Draft) -> dict:
        metric = self.db.latest_metric(d.channel_message_id) if d.channel_message_id else None
        return {
            "id": d.id,
            "status": d.status.value,
            "title": d.title,
            "text": d.draft_text,
            "source": d.source_name,
            "link": d.link,
            "created_at": _iso_z(d.created_at) if d.created_at else None,
            "published_at": _iso_z(d.published_at) if d.published_at else None,
            "channel_message_id": d.channel_message_id,
            "channel_url": self._channel_url(d.channel_message_id),
            "views": metric["views"] if metric else None,
            "forwards": metric["forwards"] if metric else None,
            "reactions": metric["reactions"] if metric else None,
        }

    def list_posts(self, status: str | None, search: str | None, limit: int, offset: int) -> dict:
        if status and status not in {s.value for s in DraftStatus}:
            raise ServiceError(400, f"Неизвестный статус: {status}")
        drafts, total = self.db.list_drafts(status, (search or "").strip() or None, limit, offset)
        return {"total": total, "items": [self._post_dict(d) for d in drafts]}

    def get_post(self, post_id: int) -> Draft:
        draft = self.db.get_draft(post_id)
        if draft is None:
            raise ServiceError(404, "Пост не найден")
        return draft

    # ---------- посты: запись ----------

    @staticmethod
    def _validate_text(text: str) -> str:
        text = (text or "").strip()
        if not text:
            raise ServiceError(400, "Текст поста пуст")
        if len(text) > MAX_TEXT_LEN:
            raise ServiceError(400, f"Текст длиннее {MAX_TEXT_LEN} символов ({len(text)})")
        return text

    def _require_telegram(self) -> TelegramAPI:
        if self.telegram is None:
            raise ServiceError(503, "TELEGRAM_BOT_TOKEN не задан — действия в канале недоступны")
        return self.telegram

    def _publish(self, draft: Draft) -> Draft:
        telegram = self._require_telegram()
        try:
            result = telegram.send_message(self.cfg.channel_chat_id, draft.draft_text, disable_web_page_preview=False)
        except TelegramAPIError as exc:
            raise ServiceError(502, f"Telegram отказал в публикации: {exc.description}") from exc
        self.db.mark_published(draft.id, result["message_id"], datetime.now(timezone.utc))
        return self.get_post(draft.id)

    def create_post(self, text: str, mode: str) -> dict:
        if mode not in CREATE_MODES:
            raise ServiceError(400, f"mode должен быть одним из: {', '.join(sorted(CREATE_MODES))}")
        text = self._validate_text(text)
        if mode == "publish":
            self._require_telegram()

        now = datetime.now(timezone.utc)
        first_line = next((ln for ln in _TAG_RE.sub("", text).splitlines() if ln.strip()), "Без заголовка")
        title = first_line.strip()[:80]
        item = FeedItem(
            source_name="Вручную", source_category="manual", source_lang="ru", title=title,
            link="", summary="", published_at=now, guid=f"manual:{uuid.uuid4().hex}",
        )
        self.db.add_item(item)
        self.db.mark_item_drafted(item.guid)  # FK drafts.item_guid -> items.guid
        status = DraftStatus.APPROVED if mode == "queue" else DraftStatus.PENDING
        draft_id = self.db.create_draft(
            Draft(id=None, item_guid=item.guid, source_name="Вручную", title=title, link="",
                  draft_text=text, tags=[], status=status, created_at=now)
        )
        draft = self.get_post(draft_id)
        if mode == "publish":
            draft = self._publish(draft)  # при отказе Telegram черновик остаётся в БД (pending)
        return self._post_dict(draft)

    def update_post(self, post_id: int, text: str | None, status: str | None) -> dict:
        draft = self.get_post(post_id)

        if status is not None:
            try:
                target = DraftStatus(status)
            except ValueError as exc:
                raise ServiceError(400, f"Неизвестный статус: {status}") from exc
            if draft.status == DraftStatus.PUBLISHED:
                raise ServiceError(409, "Опубликованному посту статус менять нельзя")
            if target not in SETTABLE_STATUSES:
                raise ServiceError(400, f"Статус {status} вручную выставлять нельзя")

        if text is not None and text.strip() != draft.draft_text:
            text = self._validate_text(text)
            if draft.status == DraftStatus.PUBLISHED:
                telegram = self._require_telegram()
                if not draft.channel_message_id:
                    raise ServiceError(409, "У поста нет id сообщения в канале")
                try:
                    telegram.edit_message_text(
                        self.cfg.channel_chat_id, draft.channel_message_id, text,
                        parse_mode="HTML", disable_web_page_preview=False,
                    )
                except TelegramAPIError as exc:
                    if "message is not modified" not in exc.description:
                        raise ServiceError(502, f"Telegram отказал в изменении: {exc.description}") from exc
            self.db.update_draft_text(post_id, text)

        if status is not None:
            self.db.update_draft_status(post_id, target)
        return self._post_dict(self.get_post(post_id))

    def delete_post(self, post_id: int, force: bool = False) -> None:
        draft = self.get_post(post_id)
        if draft.status == DraftStatus.PUBLISHED and draft.channel_message_id:
            telegram = self._require_telegram()
            deleted = telegram.delete_message(self.cfg.channel_chat_id, draft.channel_message_id)
            if not deleted and not force:
                raise ServiceError(
                    502,
                    "Не удалось удалить сообщение в канале (возможно, оно уже удалено или Telegram отказал). "
                    "Можно удалить только запись из панели.",
                )
        if draft.admin_message_id and draft.admin_chat_id and self.telegram is not None:
            # убираем сообщение с кнопками из админ-чата, чтобы на удалённый черновик нельзя было нажать
            self.telegram.delete_message(draft.admin_chat_id, draft.admin_message_id)
        self.db.delete_draft(post_id)

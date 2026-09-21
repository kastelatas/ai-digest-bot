"""Шаг 1-2 пайплайна: собрать новости, отсеять дубли, попросить LLM написать
черновик и отправить его на одобрение в админ-чат.

Публикация одобренного — в publish.py (отдельный шаг, чтобы слот публикации
не зависел от момента сбора новостей).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import Database
from .dedup import find_duplicate
from .fetch import fetch_all
from .llm import DraftContent, Summarizer
from .models import Draft, DraftStatus, FeedItem
from .telegram_api import TelegramAPI, TelegramAPIError, approve_reject_keyboard

logger = logging.getLogger(__name__)


@dataclass
class FetchRunStats:
    fetched: int = 0
    too_old: int = 0
    duplicate: int = 0
    llm_declined: int = 0
    llm_failed: int = 0
    drafted: int = 0
    errors: list[str] = field(default_factory=list)


def _is_too_old(item: FeedItem, max_age_hours: int, now: datetime) -> bool:
    if item.published_at is None:
        return False  # источник не дал дату — не отсеиваем по возрасту
    age = now - item.published_at
    return age > timedelta(hours=max_age_hours)


def run_fetch_and_draft(
    cfg: Config,
    db: Database,
    summarizer: Summarizer,
    telegram: TelegramAPI | None,
    fetcher=fetch_all,
    now: datetime | None = None,
) -> FetchRunStats:
    """Полный проход: fetch -> dedup -> LLM -> отправка на одобрение.

    telegram=None удобно для тестов/демо-прогона без сети — черновики
    просто создаются в БД со статусом PENDING без отправки сообщения.
    """
    now = now or datetime.now(timezone.utc)
    stats = FetchRunStats()

    items = fetcher([s for s in cfg.sources if s.enabled])
    stats.fetched = len(items)

    recent_titles = db.recent_titles(cfg.topic_cooldown_hours)

    for item in items:
        if db.item_exists(item.guid):
            continue  # уже видели эту конкретную ссылку

        if _is_too_old(item, cfg.max_item_age_hours, now):
            db.add_item(item)
            db.mark_item_status(item.guid, "ignored")
            stats.too_old += 1
            continue

        dup = find_duplicate(item.title, recent_titles)
        db.add_item(item)
        if dup:
            db.mark_item_status(item.guid, "duplicate")
            stats.duplicate += 1
            logger.info("Дубль: %r похоже на уже виденное %r", item.title, dup)
            continue

        recent_titles.append(item.title)  # чтобы не задублировать и внутри этого же прогона

        try:
            content: DraftContent = summarizer.summarize(item)
        except Exception as exc:  # noqa: BLE001 — сбой LLM не должен ронять весь прогон
            logger.exception("LLM упал на %r", item.title)
            db.mark_item_status(item.guid, "ignored")
            stats.llm_failed += 1
            stats.errors.append(f"{item.title}: {exc}")
            continue

        if not content.ok:
            db.mark_item_status(item.guid, "ignored")
            stats.llm_declined += 1
            logger.info("LLM отказался писать пост по %r: %s", item.title, content.reason)
            continue

        db.mark_item_status(item.guid, "drafted")
        draft = Draft(
            id=None,
            item_guid=item.guid,
            source_name=item.source_name,
            title=item.title,
            link=item.link,
            draft_text=content.as_post_text(item.link),
            tags=content.tags or [],
            status=DraftStatus.PENDING,
            created_at=now,
        )
        draft_id = db.create_draft(draft)
        stats.drafted += 1

        if telegram is not None:
            _send_for_approval(cfg, db, telegram, draft_id, draft.draft_text)

    return stats


def _send_for_approval(cfg: Config, db: Database, telegram: TelegramAPI, draft_id: int, text: str) -> None:
    admin_text = f"{text}\n\n— черновик #{draft_id}, ждёт решения —"
    try:
        result = telegram.send_message(
            cfg.admin_chat_id,
            admin_text,
            reply_markup=approve_reject_keyboard(draft_id),
        )
        db.set_admin_message(draft_id, cfg.admin_chat_id, result["message_id"])
    except TelegramAPIError:
        logger.exception("Не удалось отправить черновик #%s в админ-чат", draft_id)

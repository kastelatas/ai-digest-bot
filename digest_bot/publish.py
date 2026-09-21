"""Публикация одобренных черновиков в канал.

Логика намеренно простая: расписание («10:00, 14:00, 19:00») задаётся не тут,
а в crontab/systemd-таймерах, которые вызывают команду `publish` ровно в эти
моменты (см. README). Внутри — только защита от превышения дневного лимита
и выбор следующего в очереди (FIFO по времени одобрения черновика).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .db import Database
from .telegram_api import TelegramAPI, TelegramAPIError

logger = logging.getLogger(__name__)


@dataclass
class PublishResult:
    published: bool
    reason: str = ""
    draft_id: int | None = None
    channel_message_id: int | None = None


def _start_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def run_publish(cfg: Config, db: Database, telegram: TelegramAPI, now: datetime | None = None) -> PublishResult:
    now = now or datetime.now(timezone.utc)

    published_today = db.posts_published_since(_start_of_day(now))
    if len(published_today) >= cfg.max_posts_per_day:
        return PublishResult(published=False, reason="daily_cap_reached")

    queue = db.approved_drafts()
    if not queue:
        return PublishResult(published=False, reason="queue_empty")

    draft = queue[0]  # самый старый одобренный — FIFO
    try:
        result = telegram.send_message(
            cfg.channel_chat_id,
            draft.draft_text,
            disable_web_page_preview=False,
        )
    except TelegramAPIError as exc:
        logger.exception("Публикация черновика #%s в канал не удалась", draft.id)
        from .models import DraftStatus

        db.update_draft_status(draft.id, DraftStatus.FAILED)
        return PublishResult(published=False, reason=f"telegram_error: {exc}", draft_id=draft.id)

    channel_message_id = result["message_id"]
    db.mark_published(draft.id, channel_message_id, now)
    logger.info("Опубликован черновик #%s как сообщение %s", draft.id, channel_message_id)
    return PublishResult(published=True, draft_id=draft.id, channel_message_id=channel_message_id)

"""Сбор метрик.

Важное ограничение Telegram Bot API: он НЕ отдаёт число просмотров поста в
канале (`message.views`) — это поле доступно только через MTProto-клиента
(Telethon/Pyrogram) под обычным пользовательским аккаунтом, а не под ботом.

Поэтому здесь два независимых пути:
  1. collect_subscriber_count() — работает всегда, через Bot API.
  2. TelethonViewsCollector — опциональный, требует отдельный user-аккаунт
     и пакет telethon. Использование личного аккаунта для автоматизации
     увеличивает риск ограничений на сам аккаунт — держите это отдельным
     "техническим" аккаунтом, не основным. Подробности в README → Метрики.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Config
from .db import Database
from .telegram_api import TelegramAPI, TelegramAPIError

logger = logging.getLogger(__name__)


def collect_subscriber_count(cfg: Config, db: Database, telegram: TelegramAPI) -> int | None:
    try:
        count = telegram.get_chat_member_count(cfg.channel_chat_id)
    except TelegramAPIError:
        logger.exception("Не удалось получить число подписчиков")
        return None
    db.record_channel_stat(count)
    return count


class TelethonViewsCollector:
    """Опциональный сборщик реальных просмотров через MTProto.

    Требует: pip install telethon, TELEGRAM_API_ID/TELEGRAM_API_HASH в .env
    и заранее авторизованную сессию (запускается один раз интерактивно,
    см. README). Импортируется лениво, чтобы остальной пайплайн работал
    без этой зависимости вообще.
    """

    def __init__(self, api_id: int, api_hash: str, session_path: str = "data/telethon.session"):
        try:
            from telethon.sync import TelegramClient  # type: ignore
        except ImportError as exc:  # pragma: no cover - зависит от окружения деплоя
            raise RuntimeError(
                "Для сбора реальных просмотров нужен пакет telethon: pip install telethon"
            ) from exc
        self._client = TelegramClient(session_path, api_id, api_hash)

    def collect(self, cfg: Config, db: Database, message_ids: list[int]) -> dict[int, int]:
        """Возвращает {channel_message_id: views} для переданных id сообщений."""
        with self._client:
            messages = self._client.get_messages(cfg.channel_chat_id, ids=message_ids)
        views_by_id: dict[int, int] = {}
        for msg in messages:
            if msg is None:
                continue
            views = getattr(msg, "views", None)
            forwards = getattr(msg, "forwards", None)
            reactions = None
            if getattr(msg, "reactions", None):
                reactions = sum(r.count for r in msg.reactions.results)
            db.record_metric_snapshot(msg.id, views, forwards, reactions)
            if views is not None:
                views_by_id[msg.id] = views
        return views_by_id


def collect_metrics_for_recent_posts(
    cfg: Config,
    db: Database,
    telegram: TelegramAPI,
    telethon_collector: "TelethonViewsCollector | None" = None,
) -> dict:
    """Собирает всё, что можно, за один вызов: подписчиков всегда,
    просмотры — если передан telethon_collector."""
    subs = collect_subscriber_count(cfg, db, telegram)
    views_collected = 0
    if telethon_collector is not None:
        posts = db.all_published()
        message_ids = [p.channel_message_id for p in posts if p.channel_message_id]
        if message_ids:
            views = telethon_collector.collect(cfg, db, message_ids)
            views_collected = len(views)
    return {"subscribers": subs, "views_collected": views_collected}

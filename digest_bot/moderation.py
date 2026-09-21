"""Обработка кнопок «Опубликовать / Правки / Отклонить» из админ-чата и
вступлений/выходов подписчиков (chat_member) для статистики по ссылкам-приглашениям.

Рассчитан на короткие запуски по cron (getUpdates с небольшим timeout),
а не на постоянно висящий процесс — офсет апдейтов хранится в БД (kv_state),
поэтому повторные запуски не обрабатывают одно и то же дважды.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from .config import Config
from .db import Database
from .invite_links import record_member_update
from .models import DraftStatus
from .telegram_api import TelegramAPI, TelegramAPIError

logger = logging.getLogger(__name__)

OFFSET_KEY = "telegram_update_offset"
# когда бот впервые начал читать chat_member — с этой даты в панели считаются вступления по ссылкам
JOINS_SINCE_KEY = "join_tracking_since"
# chat_member по умолчанию не приходит — его нужно запросить явно (и бот должен быть админом канала)
ALLOWED_UPDATES = ["callback_query", "chat_member"]

_ACTIONS = {
    "approve": DraftStatus.APPROVED,
    "reject": DraftStatus.REJECTED,
    "edit": DraftStatus.NEEDS_EDIT,
}

_STATUS_LABELS = {
    DraftStatus.APPROVED: "✅ Одобрено, в очереди на публикацию",
    DraftStatus.REJECTED: "❌ Отклонено",
    DraftStatus.NEEDS_EDIT: "✏️ Нужны правки — отредактируйте draft_text в БД вручную",
}


@dataclass
class ModerationStats:
    processed: int = 0
    approved: int = 0
    rejected: int = 0
    needs_edit: int = 0
    ignored_unauthorized: int = 0
    joins: int = 0
    leaves: int = 0
    errors: list[str] = field(default_factory=list)


def process_admin_updates(cfg: Config, db: Database, telegram: TelegramAPI, timeout: int = 10) -> ModerationStats:
    stats = ModerationStats()

    if db.get_state(JOINS_SINCE_KEY) is None:
        db.set_state(JOINS_SINCE_KEY, datetime.utcnow().isoformat())

    offset_raw = db.get_state(OFFSET_KEY)
    offset = int(offset_raw) + 1 if offset_raw else None

    try:
        updates = telegram.get_updates(offset=offset, timeout=timeout, allowed_updates=ALLOWED_UPDATES)
    except TelegramAPIError as exc:
        logger.exception("getUpdates упал")
        stats.errors.append(str(exc))
        return stats

    last_update_id = None
    for update in updates:
        last_update_id = update["update_id"]
        if "chat_member" in update:
            _handle_member_update(cfg, db, update, stats)
            continue
        callback = update.get("callback_query")
        if not callback:
            continue
        _handle_callback(cfg, db, telegram, callback, stats)

    if last_update_id is not None:
        db.set_state(OFFSET_KEY, str(last_update_id))

    return stats


def _handle_member_update(cfg: Config, db: Database, update: dict, stats: ModerationStats) -> None:
    try:
        kind = record_member_update(cfg, db, update)
    except Exception as exc:  # битый апдейт не должен ронять обработку кнопок
        logger.exception("chat_member: не удалось обработать апдейт %s", update.get("update_id"))
        stats.errors.append(str(exc))
        return
    if kind == "join":
        stats.joins += 1
    elif kind == "leave":
        stats.leaves += 1


def _handle_callback(cfg: Config, db: Database, telegram: TelegramAPI, callback: dict, stats: ModerationStats) -> None:
    stats.processed += 1
    callback_id = callback["id"]
    from_user_id = callback.get("from", {}).get("id")
    data = callback.get("data", "")

    if cfg.allowed_user_ids and from_user_id not in cfg.allowed_user_ids:
        stats.ignored_unauthorized += 1
        _safe_answer(telegram, callback_id, "У вас нет прав модерировать этот канал.")
        return

    action, _, draft_id_raw = data.partition(":")
    if action not in _ACTIONS or not draft_id_raw.isdigit():
        _safe_answer(telegram, callback_id, "Неизвестное действие.")
        return

    draft_id = int(draft_id_raw)
    draft = db.get_draft(draft_id)
    if draft is None:
        _safe_answer(telegram, callback_id, "Черновик не найден (возможно, уже обработан).")
        return

    if draft.status != DraftStatus.PENDING:
        _safe_answer(telegram, callback_id, f"Уже обработано: {draft.status.value}")
        return

    new_status = _ACTIONS[action]
    db.update_draft_status(draft_id, new_status)

    if new_status == DraftStatus.APPROVED:
        stats.approved += 1
    elif new_status == DraftStatus.REJECTED:
        stats.rejected += 1
    elif new_status == DraftStatus.NEEDS_EDIT:
        stats.needs_edit += 1

    label = _STATUS_LABELS[new_status]
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        try:
            telegram.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            original_text = message.get("text", "")
            telegram.edit_message_text(chat_id, message_id, f"{original_text}\n\n{label}")
        except TelegramAPIError:
            logger.exception("Не удалось обновить сообщение черновика #%s", draft_id)

    _safe_answer(telegram, callback_id, label)


def _safe_answer(telegram: TelegramAPI, callback_id: str, text: str) -> None:
    try:
        telegram.answer_callback_query(callback_id, text)
    except TelegramAPIError:
        logger.warning("Не удалось ответить на callback_query %s", callback_id)

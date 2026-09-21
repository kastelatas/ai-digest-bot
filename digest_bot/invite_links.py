"""Ссылки-приглашения для закупов рекламы и учёт вступлений по ним.

Идея: на каждую закупку создаётся своя ссылка (createChatInviteLink), она
вставляется в рекламный пост. Когда человек вступает в канал, Telegram шлёт боту
апдейт chat_member с полем invite_link — по нему мы знаем, какая закупка привела
подписчика. Выходы из канала ссылку не содержат, поэтому уход привязываем к
последнему вступлению этого человека (мы храним его user_id).

Условия: бот — админ канала с правом «Приглашать пользователей по ссылкам», а
`moderate` идёт по cron (он читает апдейты chat_member вместе с кнопками).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Config
from .db import Database
from .telegram_api import TelegramAPI

logger = logging.getLogger(__name__)

NAME_MAX = 64
MEMBER_STATUSES = {"member", "administrator", "creator"}


def create_link(
    cfg: Config, db: Database, telegram: TelegramAPI, name: str, *,
    ad_text: str = "", cost: float | None = None, currency: str | None = None, notes: str = "",
) -> dict:
    """Создаёт ссылку в Telegram и запись в БД. TelegramAPIError пробрасывается наружу."""
    name = name.strip()
    result = telegram.create_chat_invite_link(cfg.channel_chat_id, name=name)
    link_id = db.add_invite_link(
        name=name, url=result["invite_link"], cost=cost, currency=(currency or cfg.ads_currency).upper(),
        ad_text=ad_text, notes=notes,
    )
    return db.get_invite_link(link_id)


def revoke_link(cfg: Config, db: Database, telegram: TelegramAPI | None, link_id: int) -> None:
    """Отзывает ссылку в Telegram (новые вступления по ней прекращаются) и помечает её в БД.

    telegram=None — только пометка в БД (ссылку уже отозвали руками в Telegram).
    Статистика вступивших сохраняется.
    """
    link = db.get_invite_link(link_id)
    if link is None:
        raise LookupError(f"Ссылка #{link_id} не найдена")
    if telegram is not None:
        telegram.revoke_chat_invite_link(cfg.channel_chat_id, link["url"])
    db.mark_invite_link_revoked(link_id)


def _is_our_channel(cfg: Config, chat: dict) -> bool:
    configured = cfg.channel_chat_id
    if configured.startswith("@"):
        return (chat.get("username") or "").lower() == configured[1:].lower()
    return str(chat.get("id")) == configured


def _is_member(member: dict | None) -> bool:
    if not member:
        return False
    status = member.get("status")
    return status in MEMBER_STATUSES or (status == "restricted" and bool(member.get("is_member")))


def record_member_update(cfg: Config, db: Database, update: dict) -> str | None:
    """Обрабатывает апдейт с ключом chat_member. Возвращает 'join', 'leave' или None (не про нас / не вход-выход)."""
    event = update.get("chat_member") or {}
    if not _is_our_channel(cfg, event.get("chat") or {}):
        return None

    was_in = _is_member(event.get("old_chat_member"))
    is_in = _is_member(event.get("new_chat_member"))
    user_id = (event.get("new_chat_member") or {}).get("user", {}).get("id")
    if user_id is None or was_in == is_in:
        return None

    when = datetime.fromtimestamp(event.get("date", 0), timezone.utc).replace(tzinfo=None)
    if is_in:
        url = (event.get("invite_link") or {}).get("invite_link")
        link = db.get_invite_link_by_url(url) if url else None
        db.record_join(update.get("update_id"), link["id"] if link else None, url, user_id, when)
        return "join"

    db.record_leave(user_id, when)
    return "leave"

"""Мини-CRM для продажи рекламы: букинг, публикация, авто-снятие по таймеру,
выручка. Всё хранится в таблице ads в той же SQLite-базе.

Про маркировку (erid и т.п.) — поле erid просто хранится в записи, сама
маркировка/договор с ОРД сюда не входит: сделайте это по своей юрисдикции,
это не универсально для СНГ (см. README и предыдущий разбор рынка).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .db import Database
from .models import AdBooking, AdStatus
from .telegram_api import TelegramAPI, TelegramAPIError

logger = logging.getLogger(__name__)


def book_ad(
    db: Database,
    advertiser: str,
    price: float,
    scheduled_at: datetime,
    duration_hours: int,
    currency: str = "USD",
    contact: str = "",
    erid: str = "",
    notes: str = "",
) -> int:
    ad = AdBooking(
        id=None,
        advertiser=advertiser,
        contact=contact,
        price=price,
        currency=currency,
        scheduled_at=scheduled_at,
        duration_hours=duration_hours,
        status=AdStatus.BOOKED,
        erid=erid,
        notes=notes,
        created_at=datetime.now(timezone.utc),
    )
    return db.add_ad(ad)


def publish_booked_ad(cfg: Config, db: Database, telegram: TelegramAPI, ad_id: int, text: str) -> int:
    """Публикует рекламный пост немедленно (вызывайте в нужный слот, например
    из cron в scheduled_at). Возвращает channel_message_id."""
    result = telegram.send_message(cfg.channel_chat_id, text, disable_web_page_preview=False)
    channel_message_id = result["message_id"]
    db.set_ad_channel_message(ad_id, channel_message_id)
    return channel_message_id


@dataclass
class AdRemovalStats:
    removed: list[int]
    failed: list[int]


def run_ad_removal_check(cfg: Config, db: Database, telegram: TelegramAPI, now: datetime | None = None) -> AdRemovalStats:
    now = now or datetime.now(timezone.utc)
    due = db.ads_due_for_removal(now)
    removed: list[int] = []
    failed: list[int] = []
    for ad in due:
        ok = True
        if ad.channel_message_id:
            ok = telegram.delete_message(cfg.channel_chat_id, ad.channel_message_id)
        if ok:
            db.mark_ad_removed(ad.id)
            removed.append(ad.id)
        else:
            failed.append(ad.id)
    return AdRemovalStats(removed=removed, failed=failed)


def revenue_between(db: Database, start: datetime, end: datetime) -> dict:
    ads = db.ads_between(start, end)
    by_currency: dict[str, float] = {}
    for ad in ads:
        if ad.status in (AdStatus.CANCELLED,):
            continue
        by_currency[ad.currency] = by_currency.get(ad.currency, 0.0) + ad.price
    return {
        "count": len([a for a in ads if a.status != AdStatus.CANCELLED]),
        "by_currency": by_currency,
        "ads": ads,
    }

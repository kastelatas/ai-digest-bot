"""Датаклассы, которыми обмениваются модули пайплайна."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ItemStatus(str, Enum):
    NEW = "new"
    DRAFTED = "drafted"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"


class DraftStatus(str, Enum):
    PENDING = "pending"          # ждёт одобрения в админ-чате
    APPROVED = "approved"        # одобрен, ждёт слота публикации
    NEEDS_EDIT = "needs_edit"    # админ попросил правки — черновик правится вручную в БД
    REJECTED = "rejected"
    PUBLISHED = "published"
    FAILED = "failed"            # LLM/публикация упали, нужна ручная проверка


class AdStatus(str, Enum):
    BOOKED = "booked"
    PUBLISHED = "published"
    REMOVED = "removed"
    PAID = "paid"
    CANCELLED = "cancelled"


@dataclass
class FeedItem:
    """Одна новость, полученная из RSS/Atom-ленты до всякой обработки."""

    source_name: str
    source_category: str
    source_lang: str
    title: str
    link: str
    summary: str
    published_at: datetime | None
    guid: str = ""

    def __post_init__(self) -> None:
        if not self.guid:
            self.guid = self.link


@dataclass
class Draft:
    id: int | None
    item_guid: str
    source_name: str
    title: str
    link: str
    draft_text: str
    tags: list[str]
    status: DraftStatus
    created_at: datetime
    admin_message_id: int | None = None
    channel_message_id: int | None = None
    published_at: datetime | None = None
    admin_chat_id: int | None = None


@dataclass
class AdBooking:
    id: int | None
    advertiser: str
    contact: str
    price: float
    currency: str
    scheduled_at: datetime
    duration_hours: int
    status: AdStatus
    erid: str = ""
    notes: str = ""
    channel_message_id: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

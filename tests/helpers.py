"""Общие тестовые заглушки: фейковый конфиг, фейковый LLM, фейковый Telegram.

Тесты используют stdlib unittest (не pytest) — так их можно гонять в любом
окружении без дополнительной установки пакетов.
"""
from __future__ import annotations

from datetime import datetime, timezone

from digest_bot.config import Config, Source
from digest_bot.llm import DraftContent
from digest_bot.models import FeedItem
from digest_bot.telegram_api import TelegramAPIError


def make_config(**overrides) -> Config:
    defaults = dict(
        channel_chat_id="@test_channel",
        channel_timezone="Europe/Kyiv",
        admin_chat_id=111,
        allowed_user_ids=[555],
        posting_slots=["10:00", "14:00", "19:00"],
        max_posts_per_day=3,
        topic_cooldown_hours=48,
        fetch_interval_minutes=30,
        max_items_per_source=15,
        max_item_age_hours=36,
        sources=[
            Source(name="Test Source", url="https://example.com/rss", lang="ru", category="ai_news"),
        ],
        llm_provider="anthropic",
        llm_model="claude-sonnet-5",
        llm_max_tokens=900,
        llm_temperature=0.3,
        llm_style="экспертный, без воды",
        ads_default_removal_hours=48,
        ads_currency="USD",
        telegram_bot_token="fake-token",
        anthropic_api_key="fake-key",
    )
    defaults.update(overrides)
    return Config(**defaults)


def make_item(
    title="Заголовок новости",
    link="https://example.com/a1",
    summary="Краткое содержание новости про AI.",
    source_name="Test Source",
    category="ai_news",
    lang="ru",
    published_at=None,
    guid=None,
) -> FeedItem:
    return FeedItem(
        source_name=source_name,
        source_category=category,
        source_lang=lang,
        title=title,
        link=link,
        summary=summary,
        published_at=published_at if published_at is not None else datetime.now(timezone.utc),
        guid=guid or link,
    )


class FakeSummarizer:
    """Детерминированная замена LLM: успех, если явно не помечено decline/fail."""

    def __init__(self, decline_titles: set[str] | None = None, fail_titles: set[str] | None = None):
        self.decline_titles = decline_titles or set()
        self.fail_titles = fail_titles or set()
        self.calls: list[str] = []

    def summarize(self, item: FeedItem) -> DraftContent:
        self.calls.append(item.title)
        if item.title in self.fail_titles:
            raise RuntimeError("симулированный сбой LLM")
        if item.title in self.decline_titles:
            return DraftContent(ok=False, reason="недостаточно фактов")
        return DraftContent(
            ok=True,
            hook=item.title[:80],
            body=f"Пересказ: {item.summary}",
            tags=[item.source_category],
        )


class FakeTelegramAPI:
    """Дублирует интерфейс TelegramAPI, но ничего не шлёт по сети.

    Используется во всех тестах пайплайна вместо настоящего клиента —
    записывает вызовы, чтобы их можно было проверить в assert-ах.
    """

    def __init__(self, subscriber_count: int = 1000, fail_send: bool = False,
                 fail_delete: bool = False, edit_error: str | None = None, fail_invite: bool = False):
        self._next_message_id = 1000
        self.sent: list[dict] = []
        self.edited_text: list[dict] = []
        self.edited_markup: list[dict] = []
        self.deleted: list[tuple] = []
        self.answered_callbacks: list[dict] = []
        self._pending_updates: list[dict] = []
        self._next_update_id = 1
        self.subscriber_count = subscriber_count
        self.fail_send = fail_send
        self.fail_delete = fail_delete
        self.edit_error = edit_error
        self.fail_invite = fail_invite
        self.invite_links: list[dict] = []
        self.revoked_links: list[str] = []
        self.last_allowed_updates: list[str] | None = None

    # ---- запись ----

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML", disable_web_page_preview=False):
        if self.fail_send:
            raise TelegramAPIError("sendMessage", "симулированная ошибка отправки", 400)
        self._next_message_id += 1
        msg_id = self._next_message_id
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup, "message_id": msg_id})
        return {"message_id": msg_id, "chat": {"id": chat_id}, "text": text}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None, parse_mode=None,
                          disable_web_page_preview=None):
        if self.edit_error:
            raise TelegramAPIError("editMessageText", self.edit_error, 400)
        self.edited_text.append({"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode})
        return {"message_id": message_id}

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        self.edited_markup.append({"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup})
        return {"message_id": message_id}

    def delete_message(self, chat_id, message_id):
        if self.fail_delete:
            return False
        self.deleted.append((chat_id, message_id))
        return True

    def answer_callback_query(self, callback_query_id, text=None):
        self.answered_callbacks.append({"id": callback_query_id, "text": text})

    def get_chat_member_count(self, chat_id):
        return self.subscriber_count

    def create_chat_invite_link(self, chat_id, name=None):
        if self.fail_invite:
            raise TelegramAPIError("createChatInviteLink", "not enough rights to manage chat invite link", 400)
        url = f"https://t.me/+fake{len(self.invite_links) + 1}"
        self.invite_links.append({"chat_id": chat_id, "name": name, "invite_link": url})
        return {"invite_link": url, "name": name}

    def revoke_chat_invite_link(self, chat_id, invite_link):
        if self.fail_invite:
            raise TelegramAPIError("revokeChatInviteLink", "invite link not found", 400)
        self.revoked_links.append(invite_link)
        return {"invite_link": invite_link, "is_revoked": True}

    def get_updates(self, offset=None, timeout=25, allowed_updates=None):
        self.last_allowed_updates = allowed_updates
        updates = self._pending_updates
        self._pending_updates = []
        return updates

    # ---- помощники для тестов ----

    def queue_callback(self, callback_data: str, from_user_id: int, chat_id, message_id, text=""):
        self._next_update_id += 1
        self._pending_updates.append(
            {
                "update_id": self._next_update_id,
                "callback_query": {
                    "id": f"cbq{self._next_update_id}",
                    "from": {"id": from_user_id},
                    "data": callback_data,
                    "message": {"chat": {"id": chat_id}, "message_id": message_id, "text": text},
                },
            }
        )

    def queue_chat_member(self, user_id: int, joined: bool, chat_username="test_channel", invite_link=None,
                          date=1_780_000_000):
        """Апдейт chat_member: joined=True — вступление (по invite_link, если задан), False — выход."""
        self._next_update_id += 1

        def member(status):
            return {"status": status, "user": {"id": user_id, "is_bot": False}}

        event = {
            "chat": {"id": -100123, "type": "channel", "username": chat_username},
            "from": {"id": user_id},
            "date": date,
            "old_chat_member": member("left" if joined else "member"),
            "new_chat_member": member("member" if joined else "left"),
        }
        if invite_link:
            event["invite_link"] = {"invite_link": invite_link, "creator": {"id": 1}}
        self._pending_updates.append({"update_id": self._next_update_id, "chat_member": event})

    def last_sent_to(self, chat_id):
        matches = [m for m in self.sent if m["chat_id"] == chat_id]
        return matches[-1] if matches else None

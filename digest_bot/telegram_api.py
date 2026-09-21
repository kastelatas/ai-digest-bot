"""Тонкая обёртка над Telegram Bot API (только то, что нужно пайплайну).

Никакого python-telegram-bot/aiogram — для нашего объёма вызовов (несколько
сообщений в день) простой REST через requests надёжнее и проще отлаживать.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 30


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int | None = None):
        self.method = method
        self.description = description
        self.error_code = error_code
        super().__init__(f"Telegram API [{method}] {error_code}: {description}")


def approve_reject_keyboard(draft_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Опубликовать", "callback_data": f"approve:{draft_id}"},
                {"text": "✏️ Правки нужны", "callback_data": f"edit:{draft_id}"},
                {"text": "❌ Отклонить", "callback_data": f"reject:{draft_id}"},
            ]
        ]
    }


class TelegramAPI:
    def __init__(self, bot_token: str, session: requests.Session | None = None):
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN не задан")
        self.bot_token = bot_token
        self.session = session or requests.Session()

    def _call(self, method: str, payload: dict[str, Any]) -> dict:
        url = f"{API_BASE}/bot{self.bot_token}/{method}"
        resp = self.session.post(url, json=payload, timeout=TIMEOUT_SECONDS)
        data = resp.json()
        if not data.get("ok"):
            raise TelegramAPIError(
                method, data.get("description", "unknown error"), data.get("error_code")
            )
        return data["result"]

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = False,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def edit_message_text(
        self, chat_id: str | int, message_id: int, text: str, reply_markup: dict | None = None
    ) -> dict:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("editMessageText", payload)

    def edit_message_reply_markup(
        self, chat_id: str | int, message_id: int, reply_markup: dict | None
    ) -> dict:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("editMessageReplyMarkup", payload)

    def delete_message(self, chat_id: str | int, message_id: int) -> bool:
        try:
            self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
            return True
        except TelegramAPIError as exc:
            logger.warning("Не удалось удалить сообщение %s/%s: %s", chat_id, message_id, exc)
            return False

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._call("answerCallbackQuery", payload)

    def get_chat_member_count(self, chat_id: str | int) -> int:
        result = self._call("getChatMemberCount", {"chat_id": chat_id})
        return int(result)

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload)

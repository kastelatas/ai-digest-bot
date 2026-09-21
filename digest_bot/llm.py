"""Написание черновика поста через LLM: Anthropic Messages API или любой
OpenAI-совместимый (по умолчанию OpenRouter). Провайдер выбирается в
config.yaml -> llm.provider (см. build_summarizer).

Зовём HTTP напрямую через requests, а не через SDK — меньше зависимостей и
проще подменить в тестах (см. tests/test_llm.py).

Защита от "мусорного вывода" модели (важно для полностью автоматических
пайплайнов, см. README): модели строго запрещено использовать факты, которых
нет в переданном тексте, и результат должен быть валидным JSON по схеме —
если модель ответит не-JSON или без обязательных полей, черновик помечается
как FAILED и уходит на ручную проверку, а не публикуется молча.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import requests

from .models import FeedItem

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT_TEMPLATE = """\
Ты — редактор-ассистент русскоязычного Telegram-канала с AI/IT-дайджестом.
Стиль канала: {style}

Тебе дают ОДНУ новость: заголовок, ссылку и выжимку исходного текста.
Твоя задача — написать короткий пост для канала на русском языке.

СТРОГИЕ ПРАВИЛА:
1. Используй только факты из переданного текста. Ничего не выдумывай и не
   добавляй "общеизвестных" деталей, которых нет в исходнике.
2. Если исходного текста мало и по нему нельзя написать содержательный пост —
   верни "ok": false с причиной в "reason".
3. Никакой рекламы, кликбейта, восклицательных знаков через один.
4. Никаких оценочных суждений от первого лица ("я считаю") — только факты и
   их значение для читателя.
5. Ответ — СТРОГО валидный JSON, без markdown-обёртки, без комментариев.

Формат ответа (JSON):
{{
  "ok": true,
  "hook": "короткий цепляющий заголовок поста, до 80 символов",
  "body": "2-4 абзаца через \\n\\n, без заголовка внутри",
  "tags": ["тег1", "тег2"],
  "reason": ""
}}
"""

USER_PROMPT_TEMPLATE = """\
Заголовок источника: {title}
Источник: {source_name}
Ссылка: {link}

Текст/выжимка:
{summary}
"""


class LLMError(RuntimeError):
    pass


def build_prompts(style: str, item: FeedItem) -> tuple[str, str]:
    """(system, user) — одинаковые для всех провайдеров."""
    system = SYSTEM_PROMPT_TEMPLATE.format(style=style)
    user = USER_PROMPT_TEMPLATE.format(
        title=item.title,
        source_name=item.source_name,
        link=item.link,
        summary=item.summary or "(выжимка отсутствует, есть только заголовок)",
    )
    return system, user


@dataclass
class DraftContent:
    ok: bool
    hook: str = ""
    body: str = ""
    tags: list[str] | None = None
    reason: str = ""

    def as_post_text(self, link: str) -> str:
        tags_line = " ".join(f"#{t}" for t in (self.tags or []))
        parts = [f"<b>{self.hook}</b>", "", self.body, "", f"Источник: {link}"]
        if tags_line:
            parts.append(tags_line)
        return "\n".join(parts)


class Summarizer(Protocol):
    def summarize(self, item: FeedItem) -> DraftContent: ...


class AnthropicSummarizer:
    """Реальный вызов Claude. Требует ANTHROPIC_API_KEY."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-5",
        max_tokens: int = 900,
        temperature: float = 0.3,
        style: str = "экспертный, без воды",
        session: requests.Session | None = None,
        timeout: int = 60,
    ):
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY не задан")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.style = style
        self.session = session or requests.Session()
        self.timeout = timeout

    def summarize(self, item: FeedItem) -> DraftContent:
        system, user = build_prompts(self.style, item)
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        try:
            resp = self.session.post(
                ANTHROPIC_API_URL, headers=headers, json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"Anthropic API недоступен: {exc}") from exc

        data = resp.json()
        try:
            text = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Неожиданный формат ответа Anthropic API: {data}") from exc

        return parse_draft_json(text)


class OpenAICompatSummarizer:
    """Любой OpenAI-совместимый /chat/completions; по умолчанию OpenRouter.

    Модель задаётся как у провайдера, для OpenRouter — "вендор/модель",
    например "google/gemini-3.1-flash-lite".
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = OPENROUTER_BASE_URL,
        max_tokens: int = 900,
        temperature: float = 0.3,
        style: str = "экспертный, без воды",
        session: requests.Session | None = None,
        timeout: int = 90,
    ):
        if not api_key:
            raise LLMError("API-ключ провайдера не задан")
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.style = style
        self.session = session or requests.Session()
        self.timeout = timeout

    def summarize(self, item: FeedItem) -> DraftContent:
        system, user = build_prompts(self.style, item)
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "digest_bot",  # подпись приложения для OpenRouter, остальным провайдерам безвредна
        }
        try:
            resp = self.session.post(self.url, headers=headers, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"LLM API недоступен: {exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(f"LLM API вернул не JSON: {resp.text[:200]}") from exc
        # OpenRouter иногда отвечает 200 с телом {"error": {...}} (лимит, сбой апстрима)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Неожиданный формат ответа LLM API: {str(data)[:300]}") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError("LLM вернул пустой ответ (возможно, модель ушла в reasoning и не уложилась в max_tokens)")

        return parse_draft_json(text)


def parse_draft_json(text: str) -> DraftContent:
    """Отдельная функция — чтобы легко тестировать разбор ответа модели
    без сети (в т.ч. кейсы битого/нестрогого JSON)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("LLM вернул невалидный JSON: %s", exc)
        return DraftContent(ok=False, reason=f"invalid_json: {exc}")

    if not isinstance(data, dict) or "ok" not in data:
        return DraftContent(ok=False, reason="missing_ok_field")

    if not data.get("ok"):
        return DraftContent(ok=False, reason=str(data.get("reason", "model_declined")))

    hook = str(data.get("hook", "")).strip()
    body = str(data.get("body", "")).strip()
    tags = data.get("tags") or []
    if not hook or not body:
        return DraftContent(ok=False, reason="missing_hook_or_body")

    return DraftContent(ok=True, hook=hook, body=body, tags=[str(t) for t in tags])


class TemplateSummarizer:
    """Офлайн-заглушка без LLM: заголовок + первые предложения выжимки.

    Полезна как демо-режим без ключа API и как baseline в тестах — реальные
    посты должны идти через AnthropicSummarizer, шаблон текст не пересказывает,
    а просто обрезает, так что для публикации в канал не годится "как есть".
    """

    def summarize(self, item: FeedItem) -> DraftContent:
        if not item.summary and not item.title:
            return DraftContent(ok=False, reason="empty_source")
        body = item.summary[:500] if item.summary else "(нет выжимки источника)"
        return DraftContent(
            ok=True,
            hook=item.title[:80],
            body=body,
            tags=[item.source_category] if item.source_category else [],
        )


def build_summarizer(cfg: "Config") -> Summarizer:
    """Выбирает провайдера по llm.provider. Нет ключа — офлайн-шаблон (демо, не для публикации)."""
    provider = (cfg.llm_provider or "anthropic").lower()
    common = dict(max_tokens=cfg.llm_max_tokens, temperature=cfg.llm_temperature, style=cfg.llm_style)
    if provider == "openrouter":
        if cfg.openrouter_api_key:
            return OpenAICompatSummarizer(
                api_key=cfg.openrouter_api_key,
                model=cfg.llm_model,
                base_url=cfg.llm_base_url or OPENROUTER_BASE_URL,
                **common,
            )
        logger.warning("OPENROUTER_API_KEY не задан — использую офлайн-шаблон (демо-режим, не для публикации как есть)")
        return TemplateSummarizer()
    if provider == "anthropic":
        if cfg.anthropic_api_key:
            return AnthropicSummarizer(api_key=cfg.anthropic_api_key, model=cfg.llm_model, **common)
        logger.warning("ANTHROPIC_API_KEY не задан — использую офлайн-шаблон (демо-режим, не для публикации как есть)")
        return TemplateSummarizer()
    raise LLMError(f"Неизвестный llm.provider: {cfg.llm_provider!r} (поддерживаются: anthropic, openrouter)")

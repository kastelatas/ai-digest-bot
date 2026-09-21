"""Загрузка config.yaml + .env без лишних зависимостей."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Минимальный парсер .env — чтобы не тянуть python-dotenv.

    Не перезаписывает переменные, уже заданные в окружении (например,
    выставленные systemd/cron/CI), это стандартное поведение dotenv.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Source:
    name: str
    url: str
    lang: str
    category: str
    weight: float = 1.0
    enabled: bool = True


@dataclass
class Config:
    channel_chat_id: str
    channel_timezone: str
    admin_chat_id: int
    allowed_user_ids: list[int]
    posting_slots: list[str]
    max_posts_per_day: int
    topic_cooldown_hours: int
    fetch_interval_minutes: int
    max_items_per_source: int
    max_item_age_hours: int
    sources: list[Source]
    llm_provider: str
    llm_model: str
    llm_max_tokens: int
    llm_temperature: float
    llm_style: str
    ads_default_removal_hours: int
    ads_currency: str

    telegram_bot_token: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)

    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def load_config(config_path: str | Path | None = None, env_path: str | Path | None = None) -> Config:
    load_dotenv(Path(env_path) if env_path else ROOT / ".env")

    path = Path(config_path) if config_path else ROOT / "config.yaml"
    if config_path and not path.exists():
        # явно заданный путь не должен тихо подменяться example-конфигом с фиктивными chat_id
        raise FileNotFoundError(f"Конфиг не найден: {path}")
    if not path.exists():
        # позволяет запускать тесты/демо без реального config.yaml
        path = ROOT / "config.example.yaml"

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    sources = [
        Source(
            name=s["name"],
            url=s["url"],
            lang=s.get("lang", "ru"),
            category=s.get("category", "general"),
            weight=float(s.get("weight", 1.0)),
            enabled=bool(s.get("enabled", True)),
        )
        for s in data.get("sources", [])
    ]

    return Config(
        channel_chat_id=str(data["channel"]["chat_id"]),
        channel_timezone=data["channel"].get("timezone", "UTC"),
        admin_chat_id=int(data["admin"]["chat_id"]),
        allowed_user_ids=[int(u) for u in data["admin"].get("allowed_user_ids", [])],
        posting_slots=list(data["posting"].get("slots", ["12:00"])),
        max_posts_per_day=int(data["posting"].get("max_posts_per_day", 3)),
        topic_cooldown_hours=int(data["posting"].get("topic_cooldown_hours", 48)),
        fetch_interval_minutes=int(data["fetch"].get("interval_minutes", 30)),
        max_items_per_source=int(data["fetch"].get("max_items_per_source", 15)),
        max_item_age_hours=int(data["fetch"].get("max_item_age_hours", 36)),
        sources=sources,
        llm_provider=data.get("llm", {}).get("provider", "anthropic"),
        llm_model=data.get("llm", {}).get("model", "claude-sonnet-5"),
        llm_max_tokens=int(data.get("llm", {}).get("max_tokens", 900)),
        llm_temperature=float(data.get("llm", {}).get("temperature", 0.3)),
        llm_style=data.get("llm", {}).get("style", ""),
        ads_default_removal_hours=int(data.get("ads", {}).get("default_removal_hours", 48)),
        ads_currency=data.get("ads", {}).get("currency", "USD"),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        raw=data,
    )

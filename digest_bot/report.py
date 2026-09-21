"""Еженедельный отчёт: посты, рост подписчиков, реклама и выручка.

build_weekly_report() возвращает готовый текст (для отправки в админ-чат)
и словарь с сырыми цифрами (для выгрузки в трекер — см. tracker/README.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .ads import revenue_between
from .config import Config
from .db import Database


@dataclass
class WeeklyReport:
    period_start: datetime
    period_end: datetime
    posts_published: int
    subscribers_start: int | None
    subscribers_end: int | None
    subscribers_delta: int | None
    ads_count: int
    ads_revenue_by_currency: dict[str, float]
    published_posts: list[dict] = field(default_factory=list)
    top_posts: list[dict] = field(default_factory=list)
    text: str = ""


def build_weekly_report(cfg: Config, db: Database, now: datetime | None = None) -> WeeklyReport:
    now = now or datetime.now(timezone.utc)
    period_start = now - timedelta(days=7)

    posts = db.posts_published_since(period_start)

    stats = db.channel_stats_since(period_start)
    subs_start = stats[0]["subscribers"] if stats else None
    subs_end = stats[-1]["subscribers"] if stats else None
    subs_delta = (subs_end - subs_start) if (subs_start is not None and subs_end is not None) else None

    revenue = revenue_between(db, period_start, now)

    published_posts = []
    top_posts = []
    # posts_published_since уже отсортирован по времени публикации (см. db.py),
    # но явной гарантии порядка в SQL нет — сортируем здесь, чтобы не зависеть от этого.
    for p in sorted(posts, key=lambda d: d.published_at or period_start):
        views = None
        if p.channel_message_id:
            metric = db.latest_metric(p.channel_message_id)
            if metric and metric.get("views") is not None:
                views = metric["views"]
        published_posts.append({"title": p.title, "link": p.link, "views": views})
        if views is not None:
            top_posts.append({"title": p.title, "views": views, "link": p.link})

    top_posts.sort(key=lambda x: x["views"], reverse=True)
    top_posts = top_posts[:5]

    report = WeeklyReport(
        period_start=period_start,
        period_end=now,
        posts_published=len(posts),
        subscribers_start=subs_start,
        subscribers_end=subs_end,
        subscribers_delta=subs_delta,
        ads_count=revenue["count"],
        ads_revenue_by_currency=revenue["by_currency"],
        published_posts=published_posts,
        top_posts=top_posts,
    )
    report.text = _render_text(report)
    return report


def _render_text(r: WeeklyReport) -> str:
    lines = [
        f"<b>Недельный отчёт</b> {r.period_start:%d.%m} — {r.period_end:%d.%m}",
        "",
        f"Постов опубликовано: {r.posts_published}",
    ]

    if r.subscribers_start is not None and r.subscribers_end is not None:
        sign = "+" if (r.subscribers_delta or 0) >= 0 else ""
        lines.append(
            f"Подписчики: {r.subscribers_start} → {r.subscribers_end} ({sign}{r.subscribers_delta})"
        )
    else:
        lines.append("Подписчики: нет данных за период (запустите collect-metrics)")

    lines.append("")
    lines.append(f"Рекламных размещений: {r.ads_count}")
    if r.ads_revenue_by_currency:
        for currency, amount in r.ads_revenue_by_currency.items():
            lines.append(f"Выручка с рекламы: {amount:.2f} {currency}")
    else:
        lines.append("Выручка с рекламы: 0")

    lines.append("")
    if r.published_posts:
        lines.append("Опубликовано за неделю:")
        for p in r.published_posts:
            views_note = f" — {p['views']} просмотров" if p["views"] is not None else ""
            lines.append(f"• {p['title']}{views_note}")
    else:
        lines.append("Опубликовано за неделю: постов не было")

    lines.append("")
    if r.top_posts:
        lines.append("Топ постов по просмотрам:")
        for i, p in enumerate(r.top_posts, 1):
            lines.append(f"{i}. {p['title']} — {p['views']} просмотров")
    else:
        lines.append(
            "Топ постов по просмотрам: нет данных (Bot API не отдаёт просмотры, "
            "подключите модуль реальных просмотров — см. README → Метрики)"
        )

    return "\n".join(lines)

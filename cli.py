#!/usr/bin/env python3
"""Единая точка входа для cron/systemd. Примеры вызовов — в README.

    python cli.py fetch              # собрать новости, отправить черновики на одобрение
    python cli.py moderate           # обработать нажатия кнопок в админ-чате
    python cli.py publish            # опубликовать один черновик из очереди (вызывать в слотах)
    python cli.py collect-metrics    # снять подписчиков (+просмотры, если настроен telethon) и сразу sync-tracker
    python cli.py sync-tracker       # дописать новые дни и брони рекламы из БД в tracker/channel_tracker.xlsx
    python cli.py ads-check          # снять рекламные посты, которым пора выйти
    python cli.py weekly-report      # собрать и отправить недельный отчёт в админ-чат
    python cli.py book-ad ...        # добавить рекламное бронирование
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from digest_bot import ads as ads_mod
from digest_bot import metrics as metrics_mod
from digest_bot import moderation as moderation_mod
from digest_bot import publish as publish_mod
from digest_bot import report as report_mod
from digest_bot import tracker_sync
from digest_bot.config import ROOT, load_config
from digest_bot.db import Database
from digest_bot.fetch import fetch_all
from digest_bot.llm import AnthropicSummarizer, TemplateSummarizer
from digest_bot.pipeline import run_fetch_and_draft
from digest_bot.telegram_api import TelegramAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cli")

DEFAULT_TRACKER = ROOT / "tracker" / "channel_tracker.xlsx"


def _build_context(args):
    cfg = load_config(args.config)
    db = Database(args.db)
    telegram = TelegramAPI(cfg.telegram_bot_token) if cfg.telegram_bot_token else None
    return cfg, db, telegram


def cmd_fetch(args) -> int:
    cfg, db, telegram = _build_context(args)
    if cfg.anthropic_api_key:
        summarizer = AnthropicSummarizer(
            api_key=cfg.anthropic_api_key,
            model=cfg.llm_model,
            max_tokens=cfg.llm_max_tokens,
            temperature=cfg.llm_temperature,
            style=cfg.llm_style,
        )
    else:
        logger.warning("ANTHROPIC_API_KEY не задан — использую офлайн-шаблон (демо-режим, не для публикации как есть)")
        summarizer = TemplateSummarizer()

    fetcher = partial(fetch_all, max_items_per_source=cfg.max_items_per_source)
    stats = run_fetch_and_draft(cfg, db, summarizer, telegram, fetcher=fetcher)
    logger.info(
        "fetch: собрано=%s старых=%s дублей=%s отказов_LLM=%s ошибок_LLM=%s черновиков=%s",
        stats.fetched, stats.too_old, stats.duplicate, stats.llm_declined, stats.llm_failed, stats.drafted,
    )
    return 0


def cmd_moderate(args) -> int:
    cfg, db, telegram = _build_context(args)
    if telegram is None:
        logger.error("TELEGRAM_BOT_TOKEN не задан")
        return 1
    stats = moderation_mod.process_admin_updates(cfg, db, telegram)
    logger.info(
        "moderate: обработано=%s одобрено=%s отклонено=%s правки=%s без_прав=%s",
        stats.processed, stats.approved, stats.rejected, stats.needs_edit, stats.ignored_unauthorized,
    )
    return 0


def cmd_publish(args) -> int:
    cfg, db, telegram = _build_context(args)
    if telegram is None:
        logger.error("TELEGRAM_BOT_TOKEN не задан")
        return 1
    result = publish_mod.run_publish(cfg, db, telegram)
    if result.published:
        logger.info("publish: опубликован черновик #%s -> сообщение %s", result.draft_id, result.channel_message_id)
    else:
        logger.info("publish: не опубликовано (%s)", result.reason)
    return 0


def cmd_collect_metrics(args) -> int:
    cfg, db, telegram = _build_context(args)
    if telegram is None:
        logger.error("TELEGRAM_BOT_TOKEN не задан")
        return 1
    result = metrics_mod.collect_metrics_for_recent_posts(cfg, db, telegram)
    logger.info("collect-metrics: %s", result)
    # замер уже лежит в БД — сбой записи в xlsx (например, файл открыт в Excel) его не теряет,
    # но отдаём ненулевой код, чтобы Планировщик заданий показал ошибку
    return _sync_tracker(cfg, db, args.xlsx)


def _sync_tracker(cfg, db, xlsx: str | Path) -> int:
    try:
        result = tracker_sync.sync_tracker(db, xlsx, cfg.channel_timezone)
    except (tracker_sync.TrackerSyncError, OSError) as exc:
        logger.error("sync-tracker: %s", exc)
        return 1
    logger.info(
        "sync-tracker: дней в Метрики=%s, броней в Продажи_рекламы=%s, без места=%s",
        result.metrics_added, result.ads_added, result.skipped_no_room,
    )
    return 0


def cmd_sync_tracker(args) -> int:
    cfg, db, _ = _build_context(args)
    return _sync_tracker(cfg, db, args.xlsx)


def cmd_ads_check(args) -> int:
    cfg, db, telegram = _build_context(args)
    if telegram is None:
        logger.error("TELEGRAM_BOT_TOKEN не задан")
        return 1
    result = ads_mod.run_ad_removal_check(cfg, db, telegram)
    logger.info("ads-check: снято=%s ошибок=%s", result.removed, result.failed)
    return 0


def cmd_weekly_report(args) -> int:
    cfg, db, telegram = _build_context(args)
    report = report_mod.build_weekly_report(cfg, db)
    print(report.text)
    if telegram is not None and not args.no_send:
        telegram.send_message(cfg.admin_chat_id, report.text)
    return 0


def cmd_book_ad(args) -> int:
    cfg, db, telegram = _build_context(args)
    scheduled_at = datetime.fromisoformat(args.at)
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    ad_id = ads_mod.book_ad(
        db,
        advertiser=args.advertiser,
        price=args.price,
        currency=args.currency,
        scheduled_at=scheduled_at,
        duration_hours=args.hours,
        contact=args.contact or "",
        erid=args.erid or "",
        notes=args.notes or "",
    )
    logger.info("book-ad: создано бронирование #%s (%s, %s %s)", ad_id, args.advertiser, args.price, args.currency)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None, help="путь к config.yaml (по умолчанию ./config.yaml)")
    parser.add_argument("--db", default="data/digest.db", help="путь к SQLite-базе")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch").set_defaults(func=cmd_fetch)
    sub.add_parser("moderate").set_defaults(func=cmd_moderate)
    sub.add_parser("publish").set_defaults(func=cmd_publish)
    xlsx_args = argparse.ArgumentParser(add_help=False)
    xlsx_args.add_argument("--xlsx", default=DEFAULT_TRACKER, help="путь к xlsx-трекеру (по умолчанию tracker/channel_tracker.xlsx)")

    sub.add_parser("collect-metrics", parents=[xlsx_args]).set_defaults(func=cmd_collect_metrics)
    sub.add_parser("sync-tracker", parents=[xlsx_args]).set_defaults(func=cmd_sync_tracker)
    sub.add_parser("ads-check").set_defaults(func=cmd_ads_check)

    p_report = sub.add_parser("weekly-report")
    p_report.add_argument("--no-send", action="store_true", help="только напечатать, не слать в Telegram")
    p_report.set_defaults(func=cmd_weekly_report)

    p_ad = sub.add_parser("book-ad")
    p_ad.add_argument("--advertiser", required=True)
    p_ad.add_argument("--price", required=True, type=float)
    p_ad.add_argument("--currency", default="USD")
    p_ad.add_argument("--at", required=True, help="ISO-время публикации, напр. 2026-09-25T10:00:00+00:00")
    p_ad.add_argument("--hours", type=int, default=48, help="через сколько часов снять пост")
    p_ad.add_argument("--contact", default="")
    p_ad.add_argument("--erid", default="")
    p_ad.add_argument("--notes", default="")
    p_ad.set_defaults(func=cmd_book_ad)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

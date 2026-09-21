"""Скачивание и разбор RSS/Atom-лент без feedparser — только requests + stdlib XML.

Не использует внешние парсер-библиотеки специально: меньше зависимостей,
меньше что может сломаться на голом VPS.
"""
from __future__ import annotations

import email.utils as eut
import logging
import re
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

from .config import Source
from .models import FeedItem

logger = logging.getLogger(__name__)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

USER_AGENT = "ai-it-digest-bot/0.1 (+https://t.me/your_channel)"
TIMEOUT_SECONDS = 20


class FetchError(RuntimeError):
    pass


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ")
    return _WS_RE.sub(" ", text).strip()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    # RFC 822 (RSS 2.0 pubDate)
    try:
        dt = eut.parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    # ISO 8601 (Atom updated/published)
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_raw(url: str, session: requests.Session | None = None) -> str:
    sess = session or requests.Session()
    resp = sess.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.text


def parse_feed(xml_text: str, source: Source) -> list[FeedItem]:
    """Разбирает и RSS 2.0, и Atom. Возвращает [] при пустом/битом фиде вместо падения —
    один сломанный источник не должен рушить весь прогон."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Не удалось распарсить фид %s: %s", source.name, exc)
        return []

    if root.tag.endswith("RDF"):
        return _parse_rss1(root, source)
    if root.tag == "rss" or root.find("channel") is not None:
        return _parse_rss2(root, source)
    if root.tag == f"{_ATOM_NS}feed":
        return _parse_atom(root, source)
    logger.warning("Неизвестный формат фида %s (корневой тег %s)", source.name, root.tag)
    return []


def _parse_rss2(root: ET.Element, source: Source) -> list[FeedItem]:
    channel = root.find("channel")
    if channel is None:
        return []
    items: list[FeedItem] = []
    for el in channel.findall("item"):
        title = strip_html((el.findtext("title") or "").strip())
        link = (el.findtext("link") or "").strip()
        guid = (el.findtext("guid") or link).strip()
        summary = strip_html(
            el.findtext(f"{_CONTENT_NS}encoded") or el.findtext("description") or ""
        )
        pub = el.findtext("pubDate") or el.findtext(f"{_DC_NS}date")
        if not title or not link:
            continue
        items.append(
            FeedItem(
                source_name=source.name,
                source_category=source.category,
                source_lang=source.lang,
                title=title,
                link=link,
                summary=summary[:2000],
                published_at=_parse_date(pub),
                guid=guid or link,
            )
        )
    return items


def _parse_rss1(root: ET.Element, source: Source) -> list[FeedItem]:
    # RSS 1.0 / RDF — редко встречается, но на всякий случай.
    rss1_ns = "{http://purl.org/rss/1.0/}"
    items: list[FeedItem] = []
    for el in root.findall(f"{rss1_ns}item"):
        title = strip_html((el.findtext(f"{rss1_ns}title") or "").strip())
        link = (el.findtext(f"{rss1_ns}link") or "").strip()
        summary = strip_html(el.findtext(f"{rss1_ns}description") or "")
        pub = el.findtext(f"{_DC_NS}date")
        if not title or not link:
            continue
        items.append(
            FeedItem(
                source_name=source.name,
                source_category=source.category,
                source_lang=source.lang,
                title=title,
                link=link,
                summary=summary[:2000],
                published_at=_parse_date(pub),
                guid=link,
            )
        )
    return items


def _parse_atom(root: ET.Element, source: Source) -> list[FeedItem]:
    items: list[FeedItem] = []
    for el in root.findall(f"{_ATOM_NS}entry"):
        title = strip_html((el.findtext(f"{_ATOM_NS}title") or "").strip())
        link_el = el.find(f"{_ATOM_NS}link[@rel='alternate']")
        if link_el is None:
            link_el = el.find(f"{_ATOM_NS}link")
        link = link_el.get("href", "").strip() if link_el is not None else ""
        guid = (el.findtext(f"{_ATOM_NS}id") or link).strip()
        summary = strip_html(
            el.findtext(f"{_ATOM_NS}content") or el.findtext(f"{_ATOM_NS}summary") or ""
        )
        pub = el.findtext(f"{_ATOM_NS}published") or el.findtext(f"{_ATOM_NS}updated")
        if not title or not link:
            continue
        items.append(
            FeedItem(
                source_name=source.name,
                source_category=source.category,
                source_lang=source.lang,
                title=title,
                link=link,
                summary=summary[:2000],
                published_at=_parse_date(pub),
                guid=guid or link,
            )
        )
    return items


def fetch_source(source: Source, session: requests.Session | None = None) -> list[FeedItem]:
    if not source.enabled:
        return []
    try:
        raw = fetch_raw(source.url, session=session)
    except requests.RequestException as exc:
        logger.warning("Источник недоступен %s (%s): %s", source.name, source.url, exc)
        return []
    return parse_feed(raw, source)


def fetch_all(
    sources: list[Source],
    session: requests.Session | None = None,
    max_items_per_source: int | None = None,
) -> list[FeedItem]:
    """max_items_per_source — брать только первые N записей каждой ленты (в RSS новые идут первыми)."""
    sess = session or requests.Session()
    all_items: list[FeedItem] = []
    for source in sources:
        items = fetch_source(source, session=sess)
        all_items.extend(items[:max_items_per_source] if max_items_per_source else items)
    return all_items

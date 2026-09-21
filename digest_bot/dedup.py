"""Дедуп по смыслу: одна и та же новость часто приходит с разных источников
под разными URL. Точный дедуп по guid/ссылке делает db.item_exists —
здесь добавляем нечёткое сравнение заголовков (difflib, только stdlib)."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

DEFAULT_SIMILARITY_THRESHOLD = 0.82


def normalize_title(title: str) -> str:
    words = _WORD_RE.findall(title.lower())
    return " ".join(words)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def is_duplicate(title: str, recent_titles: list[str], threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> bool:
    """True, если title достаточно похож на что-то из recent_titles."""
    norm = normalize_title(title)
    if not norm:
        return False
    for other in recent_titles:
        if similarity(title, other) >= threshold:
            return True
    return False


def find_duplicate(title: str, recent_titles: list[str], threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> str | None:
    """Возвращает похожий заголовок, если он есть, иначе None. Полезно для логов."""
    best_match: str | None = None
    best_score = 0.0
    for other in recent_titles:
        score = similarity(title, other)
        if score > best_score:
            best_score = score
            best_match = other
    if best_score >= threshold:
        return best_match
    return None

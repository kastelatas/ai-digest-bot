"""Пароль администратора и подписанные cookie-сессии (без внешних зависимостей)."""
from __future__ import annotations

import hashlib
import hmac
import time

COOKIE_NAME = "digest_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 300


class Authenticator:
    def __init__(self, password: str, secret_extra: str = ""):
        if not password:
            raise RuntimeError("ADMIN_PASSWORD не задан — панель умеет публиковать и удалять посты, без пароля она не запускается")
        self._password = password
        # ключ подписи привязан к паролю: смена пароля разлогинивает все сессии
        self._key = hashlib.sha256(f"digest-web|{password}|{secret_extra}".encode()).digest()
        self._failures: dict[str, list[float]] = {}

    def check_password(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self._password.encode())

    # ----- защита от перебора -----
    def is_blocked(self, client: str, now: float | None = None) -> bool:
        now = now or time.time()
        recent = [t for t in self._failures.get(client, []) if now - t < FAILURE_WINDOW_SECONDS]
        self._failures[client] = recent
        return len(recent) >= MAX_FAILURES

    def register_failure(self, client: str, now: float | None = None) -> None:
        self._failures.setdefault(client, []).append(now or time.time())

    def clear_failures(self, client: str) -> None:
        self._failures.pop(client, None)

    # ----- сессии -----
    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue_token(self, now: float | None = None) -> str:
        expires = int((now or time.time()) + SESSION_TTL_SECONDS)
        return f"{expires}.{self._sign(str(expires))}"

    def verify_token(self, token: str | None, now: float | None = None) -> bool:
        if not token or "." not in token:
            return False
        expires_raw, _, signature = token.partition(".")
        if not expires_raw.isdigit() or not hmac.compare_digest(signature, self._sign(expires_raw)):
            return False
        return int(expires_raw) > (now or time.time())

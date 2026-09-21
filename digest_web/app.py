"""HTTP API + раздача собранного React-фронтенда.

Запуск: uvicorn --factory digest_web.app:create_app --host 0.0.0.0 --port 8080
Обязательная переменная окружения: ADMIN_PASSWORD.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from digest_bot.config import Config, load_config
from digest_bot.db import Database
from digest_bot.telegram_api import TelegramAPI

from .auth import COOKIE_NAME, SESSION_TTL_SECONDS, Authenticator
from .service import PanelService, ServiceError

logger = logging.getLogger("digest_web")

DEFAULT_DB_PATH = "data/digest.db"


class LoginBody(BaseModel):
    password: str


class CreatePostBody(BaseModel):
    text: str
    mode: str = Field("draft", description="draft | queue | publish")


class UpdatePostBody(BaseModel):
    text: str | None = None
    status: str | None = None


def create_app(
    cfg: Config | None = None,
    db: Database | None = None,
    telegram: TelegramAPI | None = None,
    password: str | None = None,
    static_dir: str | Path | None = None,
    cookie_secure: bool | None = None,
) -> FastAPI:
    cfg = cfg or load_config()
    db = db or Database(os.environ.get("DIGEST_DB_PATH", DEFAULT_DB_PATH))
    if telegram is None and cfg.telegram_bot_token:
        telegram = TelegramAPI(cfg.telegram_bot_token)
    auth = Authenticator(password if password is not None else os.environ.get("ADMIN_PASSWORD", ""),
                         secret_extra=cfg.telegram_bot_token)
    service = PanelService(cfg, db, telegram)
    secure_cookie = cookie_secure if cookie_secure is not None else os.environ.get("COOKIE_SECURE") == "1"

    app = FastAPI(title="Digest bot panel", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    def require_auth(request: Request) -> None:
        if not auth.verify_token(request.cookies.get(COOKIE_NAME)):
            raise HTTPException(status_code=401, detail="Требуется вход")

    # ---------- сессия ----------

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response):
        client = request.client.host if request.client else "unknown"
        if auth.is_blocked(client):
            raise HTTPException(status_code=429, detail="Слишком много неудачных попыток, подождите несколько минут")
        if not auth.check_password(body.password):
            auth.register_failure(client)
            raise HTTPException(status_code=401, detail="Неверный пароль")
        auth.clear_failures(client)
        response.set_cookie(
            COOKIE_NAME, auth.issue_token(), max_age=SESSION_TTL_SECONDS,
            httponly=True, samesite="strict", secure=secure_cookie, path="/",
        )
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request):
        return {
            "authenticated": auth.verify_token(request.cookies.get(COOKIE_NAME)),
            "telegram_enabled": telegram is not None,
            "channel": cfg.channel_chat_id,
            "timezone": cfg.channel_timezone,
        }

    # ---------- метрики ----------

    @app.get("/api/overview", dependencies=[Depends(require_auth)])
    def overview(days: int = Query(30, ge=1, le=365)):
        return service.overview(days)

    @app.get("/api/subscribers", dependencies=[Depends(require_auth)])
    def subscribers(days: int = Query(30, ge=1, le=365)):
        return service.subscribers_series(days)

    @app.get("/api/posts/daily", dependencies=[Depends(require_auth)])
    def posts_daily(days: int = Query(30, ge=1, le=365)):
        return {"days": days, "daily": service.posts_daily(days)}

    # ---------- посты ----------

    @app.get("/api/posts", dependencies=[Depends(require_auth)])
    def list_posts(
        status: str | None = None,
        q: str | None = None,
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return service.list_posts(status or None, q, limit, offset)

    @app.post("/api/posts", status_code=201, dependencies=[Depends(require_auth)])
    def create_post(body: CreatePostBody):
        return service.create_post(body.text, body.mode)

    @app.patch("/api/posts/{post_id}", dependencies=[Depends(require_auth)])
    def update_post(post_id: int, body: UpdatePostBody):
        return service.update_post(post_id, body.text, body.status)

    @app.delete("/api/posts/{post_id}", status_code=204, dependencies=[Depends(require_auth)])
    def delete_post(post_id: int, force: bool = False):
        service.delete_post(post_id, force)
        return Response(status_code=204)

    # ---------- фронтенд ----------

    static_path = Path(static_dir or os.environ.get("STATIC_DIR", "static"))
    if static_path.is_dir():
        assets = static_path / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index = static_path / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (static_path / path).resolve()
            if path and candidate.is_file() and static_path.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app

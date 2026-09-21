# Веб-панель: React (собирается на этапе 1) + FastAPI (этап 2) в одном образе.
# Сборка:  docker compose build     Запуск:  docker compose up -d

# ---------- этап 1: сборка фронтенда ----------
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- этап 2: бэкенд + статика ----------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATIC_DIR=/app/static \
    DIGEST_DB_PATH=/app/data/digest.db
WORKDIR /app

COPY digest_web/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY digest_bot ./digest_bot
COPY digest_web ./digest_web
COPY --from=frontend /frontend/dist ./static

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"

# --factory: create_app() сам читает ADMIN_PASSWORD и падает с понятной ошибкой, если он не задан
CMD ["uvicorn", "--factory", "digest_web.app:create_app", "--host", "0.0.0.0", "--port", "8080"]

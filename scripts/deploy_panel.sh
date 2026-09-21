#!/usr/bin/env bash
# Разворачивает веб-панель (Docker + HTTPS) на сервере рядом с ботом. Запускать НА СЕРВЕРЕ под root:
#   bash /opt/ai-digest-bot/scripts/deploy_panel.sh
#   PANEL_DOMAIN=panel.example.com bash scripts/deploy_panel.sh   # свой домен вместо sslip.io
#
# Идемпотентно: ставит Docker, если его нет; генерирует ADMIN_PASSWORD, если он не задан
# (пароль НЕ печатается — лежит только в .env); открывает 80/443 в ufw, если он включён;
# собирает и перезапускает контейнеры. Код обновляйте через git pull (deploy_server.sh делает это сам).
set -euo pipefail

APP_DIR=/opt/ai-digest-bot
cd "$APP_DIR"
[ "$(id -u)" -eq 0 ] || { echo "Запустите под root"; exit 1; }
[ -f .env ] && [ -f config.yaml ] || { echo "Нужны .env и config.yaml в $APP_DIR (см. deploy_server.sh)"; exit 1; }

echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker compose version >/dev/null

echo "==> Пароль панели"
if grep -Eq '^ADMIN_PASSWORD=.+' .env; then
  echo "ADMIN_PASSWORD уже задан в .env"
else
  PW=$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n' | head -c 24)
  sed -i '/^ADMIN_PASSWORD=/d' .env
  printf '\nADMIN_PASSWORD=%s\n' "$PW" >> .env
  chmod 600 .env
  echo "ADMIN_PASSWORD сгенерирован и записан в .env (в вывод не выводится)"
  unset PW
fi

echo "==> Домен"
if [ -n "${PANEL_DOMAIN:-}" ]; then
  DOMAIN="$PANEL_DOMAIN"
elif grep -Eq '^PANEL_DOMAIN=.+' .env; then
  DOMAIN=$(grep -E '^PANEL_DOMAIN=' .env | tail -1 | cut -d= -f2-)
else
  IP=$(curl -4 -fsS --max-time 10 https://api.ipify.org)
  DOMAIN="${IP//./-}.sslip.io"
fi
sed -i '/^PANEL_DOMAIN=/d' .env
printf 'PANEL_DOMAIN=%s\n' "$DOMAIN" >> .env
echo "Домен панели: $DOMAIN"

echo "==> Файрвол"
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && echo "ufw: 80 и 443 открыты"
else
  echo "ufw не активен — порты не трогаю (проверьте файрвол хостинг-провайдера: нужны 80 и 443)"
fi

echo "==> Сборка и запуск"
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build

echo "==> Проверка"
for i in $(seq 1 30); do
  if curl -fsS --max-time 5 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then echo "панель отвечает"; break; fi
  sleep 2
  [ "$i" = 30 ] && { echo "панель не поднялась:"; docker compose logs --tail 30 panel; exit 1; }
done
echo "Ссылка: https://$DOMAIN   (сертификат выпускается при первом обращении, до минуты)"
echo "Пароль:  ssh root@<сервер> \"grep ^ADMIN_PASSWORD= $APP_DIR/.env\""

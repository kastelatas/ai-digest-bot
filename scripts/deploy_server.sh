#!/usr/bin/env bash
# Разворачивает digest_bot на Linux-сервере (Debian/Ubuntu) и прописывает cron.
# Запускать НА СЕРВЕРЕ под root, после загрузки архива проекта:
#
#   bash deploy_server.sh [/путь/к/bot.tgz]       # по умолчанию /opt/bot.tgz
#   SET_TZ=1 bash deploy_server.sh                 # ещё и выставить часовой пояс сервера как у канала
#
# Повторный запуск безопасен: код обновляется, а .env, data/ и logs/ остаются.
# Слоты publish берутся из config.yaml -> posting.slots.
set -euo pipefail

ARCHIVE="${1:-/opt/bot.tgz}"
APP_DIR=/opt/ai-digest-bot
MARK_BEGIN="# >>> ai-digest-bot >>>"
MARK_END="# <<< ai-digest-bot <<<"

[ "$(id -u)" -eq 0 ] || { echo "Запустите под root"; exit 1; }
[ -f "$ARCHIVE" ] || { echo "Не найден архив $ARCHIVE (загрузите его через scp)"; exit 1; }

echo "==> Системные пакеты"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv cron >/dev/null

echo "==> Распаковка в $APP_DIR"
mkdir -p /opt
tar xzf "$ARCHIVE" -C /opt
cd "$APP_DIR"
[ -f .env ] || { echo "В архиве нет .env"; exit 1; }
[ -f config.yaml ] || { echo "В архиве нет config.yaml"; exit 1; }
chmod 600 .env
mkdir -p logs data

echo "==> venv и зависимости"
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

echo "==> Часовой пояс"
CHANNEL_TZ=$(venv/bin/python -c "from digest_bot.config import load_config; print(load_config().channel_timezone)")
SERVER_TZ=$(timedatectl show -p Timezone --value 2>/dev/null || echo unknown)
if [ "$SERVER_TZ" != "$CHANNEL_TZ" ]; then
  if [ "${SET_TZ:-0}" = "1" ]; then
    timedatectl set-timezone "$CHANNEL_TZ"
    echo "Часовой пояс сервера: $SERVER_TZ -> $CHANNEL_TZ"
  else
    echo "ВНИМАНИЕ: пояс сервера $SERVER_TZ, канала $CHANNEL_TZ — слоты publish сработают не в то время."
    echo "          Перезапустите с SET_TZ=1 или выполните: timedatectl set-timezone $CHANNEL_TZ"
  fi
fi

echo "==> cron"
# fetch без ключа Anthropic засыпал бы админ-чат сырыми черновиками — отключаем его до появления ключа
FETCH_PREFIX=""
if ! grep -Eq '^ANTHROPIC_API_KEY=.+' .env; then
  FETCH_PREFIX="#"
  echo "ВНИМАНИЕ: ANTHROPIC_API_KEY пуст — задача fetch закомментирована. Впишите ключ в .env и запустите скрипт снова."
fi

PUBLISH_LINES=$(venv/bin/python - <<'PY'
from digest_bot.config import load_config
for slot in load_config().posting_slots:
    h, m = slot.split(":")
    print(f"{int(m)} {int(h)} * * * cd /opt/ai-digest-bot && venv/bin/python cli.py publish >> logs/publish.log 2>&1")
PY
)

TMP=$(mktemp)
crontab -l 2>/dev/null | sed "/$MARK_BEGIN/,/$MARK_END/d" > "$TMP" || true
{
  echo "$MARK_BEGIN"
  echo "${FETCH_PREFIX}*/30 * * * * cd $APP_DIR && venv/bin/python cli.py fetch >> logs/fetch.log 2>&1"
  echo "* * * * * cd $APP_DIR && venv/bin/python cli.py moderate >> logs/moderate.log 2>&1"
  echo "$PUBLISH_LINES"
  echo "0 * * * * cd $APP_DIR && venv/bin/python cli.py ads-check >> logs/ads.log 2>&1"
  echo "50 23 * * * cd $APP_DIR && venv/bin/python cli.py collect-metrics >> logs/metrics.log 2>&1"
  echo "0 9 * * 1 cd $APP_DIR && venv/bin/python cli.py weekly-report >> logs/report.log 2>&1"
  echo "$MARK_END"
} >> "$TMP"
crontab "$TMP"
rm -f "$TMP"
systemctl enable --now cron >/dev/null 2>&1 || true

echo "==> Проверка: cli.py moderate"
if venv/bin/python cli.py moderate; then
  echo "OK. Расписание установлено:"
  crontab -l | sed -n "/$MARK_BEGIN/,/$MARK_END/p"
else
  echo "moderate завершился с ошибкой — проверьте токен в .env и доступ сервера к api.telegram.org"
  exit 1
fi

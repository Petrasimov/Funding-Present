#!/bin/bash
# deploy.sh — Первый деплой Funding-Present на VPS.
# Запускается ОДИН РАЗ. Дальше используй update.sh.
#
# Запуск:
#   chmod +x deploy.sh
#   ./deploy.sh

set -e  # остановить при первой ошибке

echo "══════════════════════════════════════════"
echo "  Funding-Present — первый деплой"
echo "══════════════════════════════════════════"

# ─── 1. Python-зависимости ────────────────────────────────────────────────────
echo ""
echo "▶ Устанавливаем Python-зависимости..."
pip install --break-system-packages aiohttp asyncpg fastapi uvicorn

# ─── 2. PostgreSQL — создание БД и пользователя ───────────────────────────────
echo ""
echo "▶ Создаём БД и пользователя PostgreSQL..."

sudo -u postgres psql <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'funding_user') THEN
        CREATE USER funding_user WITH PASSWORD 'funding_pass';
        RAISE NOTICE 'Пользователь funding_user создан';
    ELSE
        RAISE NOTICE 'Пользователь funding_user уже существует';
    END IF;
END
$$;

SELECT 'CREATE DATABASE funding' WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'funding'
)\gexec

GRANT ALL PRIVILEGES ON DATABASE funding TO funding_user;
SQL

# Разрешаем доступ к схеме public
sudo -u postgres psql -d funding -c "GRANT ALL ON SCHEMA public TO funding_user;"

# ─── 3. Применяем схему таблиц ────────────────────────────────────────────────
echo ""
echo "▶ Создаём таблицы..."
psql -U funding_user -d funding -h 127.0.0.1 -f schema.sql
echo "  Таблицы созданы."

# ─── 4. Создаём папку для логов ───────────────────────────────────────────────
echo ""
echo "▶ Создаём папку логов..."
mkdir -p /home/axioma/logs

# ─── 5. Запускаем API-сервер ──────────────────────────────────────────────────
echo ""
echo "▶ Запускаем API-сервер (порт 5001)..."
nohup python api_server.py > /home/axioma/logs/funding-api.log 2>&1 &
echo "  PID: $!"
echo $! > /home/axioma/logs/funding-api.pid

# ─── 6. Запускаем пайплайн ────────────────────────────────────────────────────
echo ""
echo "▶ Запускаем пайплайн (run.py)..."
nohup python run.py > /home/axioma/logs/funding-run.log 2>&1 &
echo "  PID: $!"
echo $! > /home/axioma/logs/funding-run.pid

# ─── 7. Проверка ──────────────────────────────────────────────────────────────
echo ""
echo "▶ Ждём 5 секунд и проверяем API..."
sleep 5
curl -s http://127.0.0.1:5001/api/funding/health && echo "" || echo "  ⚠ API ещё не поднялся, проверь лог: tail -f /home/axioma/logs/funding-api.log"

echo ""
echo "══════════════════════════════════════════"
echo "  Деплой завершён!"
echo "══════════════════════════════════════════"
echo ""
echo "Логи:"
echo "  API:      tail -f /home/axioma/logs/funding-api.log"
echo "  Pipeline: tail -f /home/axioma/logs/funding-run.log"
echo ""
echo "Следующий шаг — добавить Nginx location /api/funding/"
echo "  (команды в README.md)"
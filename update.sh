#!/bin/bash
# update.sh — Обновление через git pull + перезапуск процессов.

set -e

echo "══════════════════════════════════════════"
echo "  Funding-Present — обновление"
echo "══════════════════════════════════════════"

echo ""
echo "▶ Получаем обновления..."
git pull origin main

echo ""
echo "▶ Останавливаем старые процессы..."

if [ -f /home/axioma/logs/funding-api.pid ]; then
    OLD_PID=$(cat /home/axioma/logs/funding-api.pid)
    kill "$OLD_PID" 2>/dev/null && echo "  API остановлен (PID $OLD_PID)" || echo "  API уже не работал"
fi

if [ -f /home/axioma/logs/funding-run.pid ]; then
    OLD_PID=$(cat /home/axioma/logs/funding-run.pid)
    kill "$OLD_PID" 2>/dev/null && echo "  Pipeline остановлен (PID $OLD_PID)" || echo "  Pipeline уже не работал"
fi

pkill -f "api_server.py" 2>/dev/null || true
pkill -f "run.py" 2>/dev/null || true
sleep 2

echo ""
echo "▶ Запускаем API-сервер..."
nohup python3 api_server.py > /home/axioma/logs/funding-api.log 2>&1 &
echo "  PID: $!"
echo $! > /home/axioma/logs/funding-api.pid

echo ""
echo "▶ Запускаем пайплайн..."
nohup python3 run.py > /home/axioma/logs/funding-run.log 2>&1 &
echo "  PID: $!"
echo $! > /home/axioma/logs/funding-run.pid

echo ""
echo "▶ Ждём 4 секунды и проверяем API..."
sleep 4
curl -s http://127.0.0.1:5001/api/funding/health && echo "" || echo "  ⚠ Проверь лог: tail -f /home/axioma/logs/funding-api.log"

echo ""
echo "══════════════════════════════════════════"
echo "  Обновление завершено!"
echo "══════════════════════════════════════════"
ps aux | grep -E "api_server|run\.py" | grep -v grep || echo "  (нет активных процессов)"
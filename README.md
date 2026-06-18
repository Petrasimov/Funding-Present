# Funding-Present

Сканер арбитражных возможностей на основе ставок финансирования (funding rate).  
Часть экосистемы **AXIOMA SCAN**.

## Что делает

Каждые 60 секунд:
1. Собирает текущие funding rate с 8 бирж
2. Фильтрует неактивные контракты
3. Строит FF-пары (Futures/Futures) и SF-список (Spot/Futures)
4. Сохраняет результат в PostgreSQL
5. Отдаёт данные через REST API на порту 5001

## Стратегии

**FF (Futures/Futures)**  
SHORT на бирже с высоким funding → LONG на бирже с низким funding  
Спред = `funding_rate_bid − funding_rate_ask`

**SF (Spot/Futures)**  
SHORT фьючерс (получаем funding) + BUY спот как хедж  
Только при положительном funding rate

## Биржи

Binance · Bybit · OKX · Gate.io · KuCoin · MEXC · BingX · Bitget

> Gate.io периодически возвращает 403 — используются данные остальных 7 бирж

## Стек

- Python 3.13
- aiohttp — асинхронные HTTP запросы
- asyncpg — PostgreSQL драйвер
- FastAPI + uvicorn — REST API сервер

## Быстрый старт

```bash
pip install -r requirements.txt
python run.py          # пайплайн (мастер-цикл)
python api_server.py   # API сервер (порт 5001)
```

## API

```
GET /api/funding/health              — статус сервера
GET /api/funding/rates               — все возможности
GET /api/funding/rates?strategy=ff   — только FF
GET /api/funding/rates?strategy=sf   — только SF
GET /api/funding/meta                — история циклов
```

## База данных

PostgreSQL, локально на порту 5432.  
БД: `funding`, пользователь: `funding_user`

Настройка подключения — `db.py` → `DB_CONFIG`

## Структура проекта

```
core/
  __init__.py
  utils.py       — общие утилиты (dump_decimal, valid, base_from_futures)
  fetchers.py    — сбор funding rate с 8 бирж
  filters.py     — фильтр активных контрактов
  strategies.py  — построение FF и SF списков
pipeline.py      — in-memory оркестратор всего цикла
db.py            — PostgreSQL: запись и чтение
api_server.py    — FastAPI сервер на порту 5001
run.py           — мастер-цикл с интервалом 60 сек
requirements.txt
```
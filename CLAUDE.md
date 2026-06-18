# CLAUDE.md — Контекст для AI-ассистента

## Что это за проект

Funding-Present — Python-сканер арбитражных возможностей по funding rate.  
Часть проекта AXIOMA SCAN (основной репозиторий: `Petrasimov/AXIOMA`).

Запускается параллельно с основным бэкендом (C# .NET 10 на порту 5000).  
Слушает на порту **5001**.

---

## Архитектура

### Pipeline (полностью in-memory)

Никаких промежуточных файлов. Данные передаются между шагами в памяти:

```
fetch_all() → filter_active() → build_ff_pairs() + build_sf_list() → merged list → DB
```

Один `aiohttp.ClientSession` на весь цикл — экономия на переподключениях.

### Модули

**`core/utils.py`** — shared helpers без зависимостей:
- `valid(v)` — исключает дефолтные ставки бирж: `0.0`, `±0.00005`
- `fmt(v)` — чистый float через Decimal round-trip
- `fmt_pct(v)` — форматирует в строку: `0.00043 → "+0.043%"`
- `dump_decimal(obj)` — JSON без научной нотации: `2.1e-05 → 0.000021`
- `base_from_futures(symbol, exchange)` — извлекает базовую монету из символа фьючерса

**`core/fetchers.py`** — funding rates:
- По одной async функции на каждую биржу
- `fetch_all(session)` — запускает все параллельно через `asyncio.gather`
- OKX требует отдельный запрос на каждый инструмент (семафор 30)
- Фильтры: только USDT-контракты, убраны USDC-base символы

**`core/filters.py`** — активность контрактов:
- `filter_active(records, session)` — убирает неторгуемые символы
- При ошибке fetch для биржи — все её записи сохраняются (safe fallback)

**`core/strategies.py`** — построение арб. пар:
- `build_ff_pairs(records)` — все комбинации бирж для каждого символа (itertools.combinations)
- `build_sf_list(records, session)` — SF с обогащением спотовыми биржами
- SF: только positive funding rate

**`pipeline.py`**:
- `run_pipeline()` — один async вызов, возвращает `list[dict]`

**`db.py`**:
- `create_pool()` — asyncpg connection pool
- `save_results()` — DELETE + INSERT в одной транзакции (atomic replace)
- `load_results(strategy, limit)` — чтение из БД
- `extra_asks` в SF хранится как JSON-строка (JSONB колонка)

**`api_server.py`**:
- FastAPI, порт 5001
- CORS открыт для всех origins
- Pool создаётся в lifespan, доступен через `app.state.pool`

**`run.py`**:
- Бесконечный цикл с `CYCLE_INTERVAL = 60` секунд
- `last_results` — fallback при падении цикла
- Ctrl+C завершает чисто после текущего цикла

---

## Структура данных

### FF запись (в pipeline)
```json
{
  "symbol": "FIDAUSDT",
  "strategy": "ff",
  "exchange_bid": "Bitget",
  "exchange_ask": "Binance",
  "funding_rate_bid": -0.007536,
  "funding_rate_ask": -0.010114,
  "spread": 0.257865
}
```

### SF запись (в pipeline)
```json
{
  "symbol": "SFPUSDTM",
  "strategy": "sf",
  "exchange_bid": "KuCoin",
  "exchange_ask_1": "Binance",
  "exchange_ask_2": "BingX",
  "exchange_ask_3": "MEXC",
  "funding_rate": 0.003937,
  "spread": 0.3937
}
```

### В БД (funding_opportunities)

FF: `exchange_ask` = вторая фьючерсная биржа, `extra_asks` = null  
SF: `exchange_ask` = первая спотовая биржа, `extra_asks` = JSON список остальных

---

## База данных

```
host:     127.0.0.1   (локально) / VPS IP (production)
port:     5432
database: funding
user:     funding_user
password: funding_pass
```

Таблицы: `funding_opportunities`, `funding_meta`  
Каждый цикл: полная замена `funding_opportunities` через DELETE + INSERT в транзакции.

---

## Форматы символов по биржам

```
Binance:  BTCUSDT
Bybit:    BTCUSDT
Bitget:   BTCUSDT
BingX:    BTC-USDT
Gate.io:  BTC_USDT
MEXC:     BTC_USDT
KuCoin:   BTCUSDTM
OKX:      BTC-USDT-SWAP
```

`base_from_futures()` в `core/utils.py` обрабатывает все форматы.

---

## Известные ограничения

- Gate.io даёт 403 без VPN — safe fallback, данные 7 бирж остаются
- Binance/Bybit могут давать 451/403 с некоторых IP (VPN США блокирует Binance)
- OKX: ~150 отдельных запросов с семафором 30
- `extra_asks` хранится как JSON-строка, не нативный Python list
- SIRENUSDT (Bitget) vs SIREN-USDT (BingX) — одна монета, разный формат символа

---

## Что планируется

- Деплой на VPS axioma-scan.ru (systemd сервис, Nginx → порт 5001)
- Интеграция в AXIOMA SCAN фронтенд (новая вкладка в Sidebar)
- Стаканы ордеров через WebSocket (не REST)
- Нормализация символов между биржами

---

## Правила разработки

1. Никаких промежуточных JSON файлов — только in-memory
2. При ошибке биржи — fallback, не падение всего цикла
3. Новые биржи добавляются в `EXCHANGE_FETCHERS` (fetchers.py) и `ACTIVE_FETCHERS` (filters.py)
4. `save_results` — всегда атомарная транзакция (DELETE + INSERT)
5. Один `aiohttp.ClientSession` на весь цикл pipeline
6. `extra_asks` передавать в БД как `json.dumps(list)`, не как list
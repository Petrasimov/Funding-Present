# Funding Rate Arbitrage Pipeline — Отчёт

> Дата: 2026-04-09

---

## Содержание

1. [Общая архитектура](#1-общая-архитектура)
2. [Схема финального цикла run.py](#2-схема-финального-цикла-runpy)
3. [Описание каждого скрипта](#3-описание-каждого-скрипта)
4. [Структура выходных файлов](#4-структура-выходных-файлов)
5. [Биржи и источники данных](#5-биржи-и-источники-данных)
6. [Технические решения и оптимизации](#6-технические-решения-и-оптимизации)
7. [Известные ограничения](#7-известные-ограничения)
8. [Запуск](#8-запуск)

---

## 1. Общая архитектура

Пайплайн состоит из **7 скриптов** и **7 промежуточных JSON-файлов**.  
Один мастер-скрипт `run.py` запускает все шаги последовательно в бесконечном цикле.

```
run.py
│
├── fetch_funding_rates.py      → funding_rates.json
├── filter_active_contracts.py  → funding_rates_v2.json
├── split_strategies.py         → ff_rates.json
│                                 sf_rates_v_1.json
├── enrich_sf_rates.py          → sf_rates_v_2.json
├── merge_rates.py              → funding_rates_v3.json
└── fetch_orderbooks.py         → funding_rates_v4.json  ← финальный
```

---

## 2. Схема финального цикла run.py

```
┌─────────────────────────────────────────────────────────────────┐
│                     run.py — бесконечный цикл                   │
│                    (остановка: Ctrl+C)                          │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                      ЦИКЛ N                              │   │
│  │                                                          │   │
│  │  [1] fetch_funding_rates.py                              │   │
│  │       8 бирж, async параллельно                         │   │
│  │       Фильтр: только USDT, только валидные ставки        │   │
│  │       └─> funding_rates.json  (~2600 записей)            │   │
│  │                     │                                    │   │
│  │  [2] filter_active_contracts.py                          │   │
│  │       8 бирж, проверка активного статуса контракта       │   │
│  │       └─> funding_rates_v2.json  (~2500 записей)         │   │
│  │                     │                                    │   │
│  │  [3] split_strategies.py                                 │   │
│  │       Разделение на две стратегии:                       │   │
│  │       FF (фьюч/фьюч) и SF (спот/фьюч)                   │   │
│  │       Расчёт спреда, сортировка по убыванию              │   │
│  │       ├─> ff_rates.json   (~500 пар)                     │   │
│  │       └─> sf_rates_v_1.json  (~1500 записей)             │   │
│  │                     │                                    │   │
│  │  [4] enrich_sf_rates.py                                  │   │
│  │       Поиск монеты на спотовых рынках 8 бирж             │   │
│  │       Удаление монет без спотового рынка                 │   │
│  │       └─> sf_rates_v_2.json  (~1350 записей)             │   │
│  │                     │                                    │   │
│  │  [5] merge_rates.py                                      │   │
│  │       Объединение FF + SF в один список                  │   │
│  │       Сортировка по spread (лучшие сверху)               │   │
│  │       └─> funding_rates_v3.json  (~1870 записей)         │   │
│  │                     │                                    │   │
│  │  [6] fetch_orderbooks.py                                 │   │
│  │       Книги ордеров для каждой арб. возможности          │   │
│  │       Дедупликация: ~9500 → ~4200 уникальных запросов    │   │
│  │       └─> funding_rates_v4.json  (~37 MB)  ← ФИНАЛ      │   │
│  │                                                          │   │
│  │  Шаг упал? → лог + продолжение с последними данными      │   │
│  │  Биржа 403? → пропуск биржи, данные остальных сохраняются│   │
│  │                                                          │   │
│  │  Лог: "CYCLE N complete in X.Xs"                        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                             │                                   │
│              немедленно запускает ЦИКЛ N+1                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Описание каждого скрипта

### `fetch_funding_rates.py`
Собирает текущие ставки финансирования со всех 8 бирж параллельно.

| Биржа   | Эндпоинт                                  | Поле ставки     |
|---------|-------------------------------------------|-----------------|
| Binance | `/fapi/v1/premiumIndex`                   | `lastFundingRate` |
| BingX   | `/swap/v2/quote/premiumIndex`             | `lastFundingRate` |
| Bitget  | `/mix/market/tickers?productType=USDT-FUTURES` | `fundingRate` |
| Bybit   | `/v5/market/tickers?category=linear`      | `fundingRate`   |
| Gate.io | `/futures/usdt/tickers`                   | `funding_rate`  |
| KuCoin  | `/contracts/active`                       | `fundingFeeRate` |
| MEXC    | `/contract/ticker`                        | `fundingRate`   |
| OKX     | `/public/funding-rate?instId=...`         | `fundingRate`   |

**Фильтры:**
- Только USDT-контракты (убраны USDC, USD, USDM)
- Убраны нулевые ставки (`0.0`, `0.00005`)
- `spread` умножается на 100 (хранится в процентах)

---

### `filter_active_contracts.py`
Проверяет каждую монету — можно ли сейчас открыть позицию.

| Биржа   | Признак активности         |
|---------|----------------------------|
| Binance | `status == "TRADING"`       |
| BingX   | `status == 1`               |
| Bitget  | `symbolStatus == "normal"`  |
| Bybit   | `status == "Trading"`       |
| Gate.io | `in_delisting == false`     |
| KuCoin  | эндпоинт `/active`          |
| MEXC    | `state == 0`                |
| OKX     | `state == "live"`           |

---

### `split_strategies.py`
Разделяет арбитражные возможности на две стратегии.

#### FF — Futures/Futures (открытие двух фьючерсных позиций)
- SHORT на бирже с **высокой** ставкой (`exchange_bid`)
- LONG на бирже с **низкой** ставкой (`exchange_ask`)
- `spread = (funding_rate_bid - funding_rate_ask) × 100`  (в %)

```
Примеры:
  bid=+0.4%,  ask=-0.2%  →  spread = 0.6%
  bid=-0.4%,  ask=-0.9%  →  spread = 0.5%
  bid=+0.4%,  ask=+0.1%  →  spread = 0.3%
```

#### SF — Spot/Futures (шорт фьючерс + покупка спота)
- Только монеты с **положительной** ставкой
- SHORT фьючерс → получаем funding
- BUY спот → хеджируем позицию
- `spread = funding_rate × 100`  (в %)

---

### `enrich_sf_rates.py`
Для каждой SF-монеты находит спотовые биржи где её можно купить.  
Монеты без спотового рынка **удаляются** из списка.

**Спотовые биржи:** Binance, BingX, Bybit, Bitget, Gate.io, KuCoin, MEXC, OKX

> MEXC требует `User-Agent: Chrome` — добавлен в сессию.

---

### `merge_rates.py`
Объединяет `ff_rates.json` и `sf_rates_v_2.json` в один файл.  
Добавляет поле `"strategy": "ff"` или `"strategy": "sf"`.  
Сортирует по `spread` по убыванию.

---

### `fetch_orderbooks.py`
Получает книги ордеров для каждой арбитражной пары.

**Дедупликация запросов:**
```
~9500 наивных запросов  →  ~4200 уникальных  (-55%)
```
Ключ кэша: `(symbol, exchange, market)` — один запрос на уникальную тройку,  
результат переиспользуется во всех записях.

**Поля в выходном файле:**

| Стратегия | Поле       | Описание                              |
|-----------|------------|---------------------------------------|
| FF        | `ask_a / bid_a` | Книга ордеров `exchange_ask` (LONG)  |
| FF        | `ask_b / bid_b` | Книга ордеров `exchange_bid` (SHORT) |
| SF        | `ask_b / bid_b` | Фьюч. книга `exchange_bid` (SHORT)   |
| SF        | `ask_a_N / bid_a_N` | Спот. книга `exchange_ask_N`    |

**User-Agent по биржам:**

| Биржа   | User-Agent         | Причина                          |
|---------|--------------------|----------------------------------|
| MEXC    | Chrome 120         | Блокирует дефолтный aiohttp UA   |
| Gate.io | дефолтный aiohttp  | Блокирует Chrome UA              |
| остальные | дефолтный aiohttp | Работают без ограничений         |

**MEXC ускорение:**
- Было: `semaphore=1` + `sleep(0.1s)` → **~5 минут**
- Стало: `semaphore=5` + retry при пустом ответе (soft rate-limit) → **~30-40 сек**

---

### `run.py`
Мастер-скрипт. Запускает все 6 шагов в бесконечном цикле.

```bash
python run.py
# Ctrl+C — остановка после завершения текущего цикла
```

**Поведение при ошибках:**
- Шаг упал → лог + продолжение со следующим шагом
- Биржа недоступна → используются последние успешные данные из файла
- `funding_rates_v4.json` обновляется только при успешном завершении шага 6

---

## 4. Структура выходных файлов

### `funding_rates_v4.json` — финальный файл

**FF-запись (Futures/Futures):**
```json
{
  "symbol": "BTCUSDT",
  "strategy": "ff",
  "exchange_bid": "Binance",
  "exchange_ask": "Bybit",
  "funding_rate_bid": -0.004854,
  "funding_rate_ask": -0.014279,
  "spread": 0.9425,
  "ask_a": [["price", "qty"], ...],
  "bid_a": [["price", "qty"], ...],
  "ask_b": [["price", "qty"], ...],
  "bid_b": [["price", "qty"], ...]
}
```

**SF-запись (Spot/Futures):**
```json
{
  "symbol": "BTCUSDTM",
  "strategy": "sf",
  "exchange_bid": "KuCoin",
  "exchange_ask_1": "Binance",
  "exchange_ask_2": "BingX",
  "funding_rate": 0.006397,
  "spread": 0.6397,
  "ask_b": [["price", "qty"], ...],
  "bid_b": [["price", "qty"], ...],
  "ask_a_1": [["price", "qty"], ...],
  "bid_a_1": [["price", "qty"], ...],
  "ask_a_2": [["price", "qty"], ...],
  "bid_a_2": [["price", "qty"], ...]
}
```

### Все файлы пайплайна

| Файл                   | Размер   | Описание                              |
|------------------------|----------|---------------------------------------|
| `funding_rates.json`   | ~247 KB  | Все ставки фандинга (USDT)            |
| `funding_rates_v2.json`| ~242 KB  | После фильтра активных контрактов     |
| `ff_rates.json`        | ~96 KB   | FF арбитражные пары                   |
| `sf_rates_v_1.json`    | ~146 KB  | SF список (только положительные)      |
| `sf_rates_v_2.json`    | ~388 KB  | SF список со спотовыми биржами        |
| `funding_rates_v3.json`| ~526 KB  | Объединённый список, сортировка       |
| `funding_rates_v4.json`| ~37 MB   | Финальный файл с книгами ордеров      |

---

## 5. Биржи и источники данных

| Биржа   | Фьюч. ставки | Активность | Спот рынок | Книга орд. |
|---------|:---:|:---:|:---:|:---:|
| Binance | ✓   | ✓   | ✓   | ✓  |
| BingX   | ✓   | ✓   | ✓   | ✓  |
| Bitget  | ✓   | ✓   | ✓   | ✓  |
| Bybit   | ✓   | ✓   | ✓   | ✓  |
| Gate.io | ✓   | ✓*  | ✓*  | ✓  |
| KuCoin  | ✓   | ✓   | ✓   | ✓  |
| MEXC    | ✓   | ✓   | ✓   | ✓  |
| OKX     | ✓   | ✓   | ✓   | ✓  |

> `*` Gate.io иногда возвращает 403 — используются последние данные.

---

## 6. Технические решения и оптимизации

### Async / aiohttp
Все HTTP-запросы асинхронные. Биржи опрашиваются параллельно.  
Семафоры ограничивают конкурентность на уровне биржи (5 запросов одновременно).

### Дедупликация запросов к orderbook
Перед запросами сканируются все записи и собирается `set` уникальных  
троек `(symbol, exchange, market)`. Каждая тройка запрашивается ровно **один раз**.

### Retry-логика
- HTTP 429 → `sleep(1 + attempt)` → повтор
- Пустой ответ 200 (soft rate-limit) → `sleep(0.3 × attempt)` → повтор
- Исключение сети → `sleep(0.5 × attempt)` → повтор
- Максимум **4 попытки**

### Числовой формат
Все float-значения хранятся в decimal-нотации (без научной записи):  
`2.116e-05` → `0.0000212`  
Реализовано через regex-постобработку JSON с `Decimal.format('f')`.

### User-Agent
| Биржа   | Header              |
|---------|---------------------|
| MEXC    | Chrome 120 UA       |
| Gate.io | дефолтный (aiohttp) |

---

## 7. Известные ограничения

| Проблема | Статус |
|----------|--------|
| Gate.io периодически отдаёт 403 | Используются последние данные |
| MEXC/KuCoin futures возвращают >20 уровней (нет limit param) | Данные полные, файл крупнее |
| 3 монеты с пустой книгой ордеров (FIL/TON MEXC, RLS Bitget) | Биржи сами отдают `bids:[]` |
| OKX требует отдельный запрос на каждый инструмент | Решено параллельным сбором |

---

## 8. Запуск

### Установка зависимостей
```bash
pip install aiohttp
```

### Одиночный запуск (все шаги по порядку)
```bash
python fetch_funding_rates.py
python filter_active_contracts.py
python split_strategies.py
python enrich_sf_rates.py
python merge_rates.py
python fetch_orderbooks.py
```

### Автоматический бесконечный цикл
```bash
python run.py
```
```
[10:45:00]  CYCLE 1 started
[10:45:00]  START  Funding rates  (fetch_funding_rates.py)
  v Binance      299 symbols
  ...
[10:45:12]  END    Funding rates  OK  [12.3s]

[10:45:12]  START  Orderbooks  (fetch_orderbooks.py)
  [   1/4268]    0.4s  OKX  spot  YB  0.42s  ok
  ...
[10:50:45]  END    Orderbooks  OK  [333.5s]

----------------------------------------------------------------------
[10:50:45]  CYCLE 1 complete in 348.2s
[10:50:45]  All steps succeeded
[10:50:45]  Output: funding_rates_v4.json
----------------------------------------------------------------------

[10:50:45]  CYCLE 2 started
...
```

### Остановка
`Ctrl+C` — цикл завершается **после** окончания текущего шага.

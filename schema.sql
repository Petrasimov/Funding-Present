-- schema.sql
-- Полная схема БД для Funding-Present.
-- Выполняется ОДИН РАЗ при первом деплое на новый сервер.
-- При обновлениях (git pull) этот файл не трогается.
--
-- Запуск:
--   psql -U funding_user -d funding -h 127.0.0.1 -f schema.sql

-- ─── funding_opportunities ────────────────────────────────────────────────────
-- Основная таблица возможностей. Полностью заменяется каждый цикл (DELETE + INSERT).

CREATE TABLE IF NOT EXISTS funding_opportunities (
    id                    SERIAL PRIMARY KEY,

    -- Идентификация возможности
    symbol                TEXT        NOT NULL,
    strategy              TEXT        NOT NULL,   -- 'ff' | 'sf'

    -- Биржи
    exchange_bid          TEXT        NOT NULL,   -- биржа short / futures
    exchange_ask          TEXT,                   -- биржа long / spot (первая)
    extra_asks            JSONB,                  -- SF: доп. спот-биржи ["BingX","KuCoin"]

    -- Ставки финансирования
    funding_rate          DOUBLE PRECISION,       -- SF: ставка фьючерсной ноги
    funding_rate_bid      DOUBLE PRECISION,       -- FF: ставка bid-биржи
    funding_rate_ask      DOUBLE PRECISION,       -- FF: ставка ask-биржи

    -- Спред
    spread                DOUBLE PRECISION NOT NULL,

    -- Время следующего начисления funding
    next_funding_time     TIMESTAMPTZ,            -- ближайшее из двух (FF) или фьючерсной ноги (SF)
    next_funding_time_bid TIMESTAMPTZ,            -- FF: время bid-биржи отдельно
    next_funding_time_ask TIMESTAMPTZ,            -- FF: время ask-биржи отдельно

    -- Метаданные
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Индексы для быстрой выборки
CREATE INDEX IF NOT EXISTS idx_opp_strategy
    ON funding_opportunities(strategy);

CREATE INDEX IF NOT EXISTS idx_opp_spread
    ON funding_opportunities(spread DESC);

CREATE INDEX IF NOT EXISTS idx_opp_next_funding
    ON funding_opportunities(next_funding_time);

CREATE INDEX IF NOT EXISTS idx_opp_updated
    ON funding_opportunities(updated_at DESC);

-- ─── funding_meta ─────────────────────────────────────────────────────────────
-- Лог завершённых циклов пайплайна. Растёт со временем (не очищается).

CREATE TABLE IF NOT EXISTS funding_meta (
    id           SERIAL PRIMARY KEY,
    cycle        INTEGER          NOT NULL,
    ff_count     INTEGER          NOT NULL DEFAULT 0,
    sf_count     INTEGER          NOT NULL DEFAULT 0,
    total_count  INTEGER          NOT NULL DEFAULT 0,
    elapsed_sec  DOUBLE PRECISION NOT NULL DEFAULT 0,
    finished_at  TIMESTAMPTZ      NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_meta_finished
    ON funding_meta(finished_at DESC);
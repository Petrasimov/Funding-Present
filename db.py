"""
db.py — PostgreSQL integration for funding rate pipeline.

Tables:
  funding_opportunities  — FF and SF records (replaced each cycle)
  funding_meta           — cycle history log

Usage:
  async with get_pool() as pool:
      await save_results(pool, results, cycle, ff_count, sf_count, elapsed)
"""

import asyncio
import asyncpg
import json
from datetime import datetime, timezone

# ─── Config ───────────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5432,
    "database": "funding",
    "user":     "funding_user",
    "password": "funding_pass",
}


# ─── Pool ─────────────────────────────────────────────────────────────────────

async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(**DB_CONFIG, min_size=1, max_size=5)


# ─── Save ─────────────────────────────────────────────────────────────────────

def _next_funding_dt(unix_sec):
    """unix seconds (float) -> aware UTC datetime, or None."""
    if unix_sec is None:
        return None
    try:
        return datetime.fromtimestamp(float(unix_sec), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _row_to_record(r: dict) -> tuple:
    symbol       = r["symbol"]
    strategy     = r["strategy"]
    exchange_bid = r["exchange_bid"]
    spread       = r["spread"]
    next_funding_time = _next_funding_dt(r.get("next_funding_time"))

    if strategy == "ff":
        exchange_ask     = r.get("exchange_ask")
        funding_rate     = None
        funding_rate_bid = r.get("funding_rate_bid")
        funding_rate_ask = r.get("funding_rate_ask")
        extra_asks       = None
        next_funding_time_bid = _next_funding_dt(r.get("next_funding_time_bid"))
        next_funding_time_ask = _next_funding_dt(r.get("next_funding_time_ask"))
    else:
        ask_keys     = sorted([k for k in r if k.startswith("exchange_ask_")])
        ask_list     = [r[k] for k in ask_keys]
        exchange_ask     = ask_list[0] if ask_list else None
        extra_asks       = json.dumps(ask_list[1:]) if len(ask_list) > 1 else None
        funding_rate     = r.get("funding_rate")
        funding_rate_bid = None
        funding_rate_ask = None
        next_funding_time_bid = None
        next_funding_time_ask = None

    return (
        symbol,
        strategy,
        exchange_bid,
        exchange_ask,
        funding_rate,
        funding_rate_bid,
        funding_rate_ask,
        spread,
        extra_asks,
        next_funding_time,
        next_funding_time_bid,
        next_funding_time_ask,
    )


async def save_results(
    pool:      asyncpg.Pool,
    results:   list[dict],
    cycle:     int,
    ff_count:  int,
    sf_count:  int,
    elapsed:   float,
) -> None:
    """
    Replace all rows in funding_opportunities with fresh results.
    Append one row to funding_meta.
    Both in a single transaction.
    """
    records = [_row_to_record(r) for r in results]
    now     = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():

            # Replace all opportunities atomically
            await conn.execute("DELETE FROM funding_opportunities")

            await conn.executemany(
                """
                INSERT INTO funding_opportunities
                  (symbol, strategy, exchange_bid, exchange_ask,
                   funding_rate, funding_rate_bid, funding_rate_ask,
                   spread, extra_asks, next_funding_time,
                   next_funding_time_bid, next_funding_time_ask, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13)
                """,
                [(*rec, now) for rec in records],
            )

            # Log cycle meta
            await conn.execute(
                """
                INSERT INTO funding_meta
                  (cycle, ff_count, sf_count, total_count, elapsed_sec, finished_at)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                cycle, ff_count, sf_count, len(results), elapsed, now,
            )

    with_time = sum(1 for r in records if r[9] is not None)
    print(f"[db] Saved {len(results)} records (cycle {cycle}) — {with_time} with next_funding_time")


# ─── Read ──────────────────────────────────────────────────────────────────────

async def load_results(
    pool:     asyncpg.Pool,
    strategy: str | None = None,
    limit:    int = 500,
) -> list[dict]:
    """
    Read opportunities from DB.
    Optional filter by strategy ('ff' or 'sf').
    """
    if strategy:
        rows = await pool.fetch(
            """
            SELECT * FROM funding_opportunities
            WHERE strategy = $1
            ORDER BY spread DESC
            LIMIT $2
            """,
            strategy, limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM funding_opportunities
            ORDER BY spread DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def load_meta(pool: asyncpg.Pool, last_n: int = 10) -> list[dict]:
    """Read last N cycle meta records."""
    rows = await pool.fetch(
        """
        SELECT * FROM funding_meta
        ORDER BY finished_at DESC
        LIMIT $1
        """,
        last_n,
    )
    return [dict(r) for r in rows]
"""
core/filters.py — Filter funding rate records to active contracts only.

Active-status logic per exchange:
  Binance  — status == "TRADING"
  BingX    — status == 1
  Bitget   — symbolStatus == "normal"
  Bybit    — status == "Trading"
  Gate.io  — in_delisting == false
  KuCoin   — /active endpoint (all returned are active)
  MEXC     — state == 0
  OKX      — state == "live", settleCcy == "USDT"
"""

import asyncio
import aiohttp


# ─── Active-contract fetchers ─────────────────────────────────────────────────

async def active_binance(session: aiohttp.ClientSession) -> set[str]:
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {s["symbol"] for s in data.get("symbols", []) if s.get("status") == "TRADING"}


async def active_bingx(session: aiohttp.ClientSession) -> set[str]:
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/contracts"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {c["symbol"] for c in data.get("data", []) if c.get("status") == 1}


async def active_bitget(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {c["symbol"] for c in data.get("data", []) if c.get("symbolStatus") == "normal"}


async def active_bybit(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.bybit.com/v5/market/instruments-info?category=linear"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["symbol"]
        for i in data.get("result", {}).get("list", [])
        if i.get("status") == "Trading"
    }


async def active_gate(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {c["name"] for c in data if not c.get("in_delisting", False)}


async def active_kucoin(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {c["symbol"] for c in data.get("data", [])}


async def active_mexc(session: aiohttp.ClientSession) -> set[str]:
    url = "https://contract.mexc.com/api/v1/contract/detail"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {c["symbol"] for c in data.get("data", []) if c.get("state") == 0}


async def active_okx(session: aiohttp.ClientSession) -> set[str]:
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["instId"]
        for i in data.get("data", [])
        if i.get("state") == "live" and i.get("settleCcy") == "USDT"
    }


ACTIVE_FETCHERS = {
    "Binance": active_binance,
    "BingX":   active_bingx,
    "Bitget":  active_bitget,
    "Bybit":   active_bybit,
    "Gate.io": active_gate,
    "KuCoin":  active_kucoin,
    "MEXC":    active_mexc,
    "OKX":     active_okx,
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


async def filter_active(records: list[dict], session: aiohttp.ClientSession) -> list[dict]:
    """
    Remove records whose contract is not currently active for trading.
    On fetch failure for an exchange — keeps all its records (safe fallback).
    """
    print("[filters] Fetching active contract lists...")

    gathered = await asyncio.gather(
        *[fetcher(session) for fetcher in ACTIVE_FETCHERS.values()],
        return_exceptions=True,
    )

    active_sets: dict[str, set[str] | None] = {}
    for exchange, result in zip(ACTIVE_FETCHERS.keys(), gathered):
        if isinstance(result, Exception):
            print(f"  x {exchange:10s}  ERROR — {result}")
            active_sets[exchange] = None  # keep all on failure
        else:
            active_sets[exchange] = result
            print(f"  v {exchange:10s}  {len(result):4d} active contracts")

    filtered, dropped = [], 0
    for entry in records:
        exchange = entry.get("exchange", "")
        symbol   = entry.get("symbol", "")
        active   = active_sets.get(exchange)
        if active is None or symbol in active:
            filtered.append(entry)
        else:
            dropped += 1

    print(f"  Removed {dropped} inactive | Kept {len(filtered)}\n")
    return filtered
"""
core/strategies.py — Build FF and SF opportunity lists from funding rate records.

FF (Futures/Futures):
  SHORT on exchange_bid (higher rate), LONG on exchange_ask (lower rate)
  spread = funding_rate_bid - funding_rate_ask  (always > 0)

SF (Spot/Futures):
  SHORT futures (collect funding), BUY spot as hedge
  Only when funding_rate > 0
  exchange_ask_1..N — spot exchanges that list the coin
"""

import asyncio
import aiohttp
from collections import defaultdict
from itertools import combinations

from core.utils import base_from_futures


# ─── FF ───────────────────────────────────────────────────────────────────────

def build_ff_pairs(records: list[dict]) -> list[dict]:
    """
    Build all Futures/Futures arbitrage pairs.
    Returns list sorted by spread descending.
    """
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_symbol[rec["symbol"]].append(rec)

    rows = []
    for symbol, entries in by_symbol.items():
        if len(entries) < 2:
            continue
        for a, b in combinations(entries, 2):
            if a["funding_rate"] >= b["funding_rate"]:
                bid, ask = a, b
            else:
                bid, ask = b, a

            spread = bid["funding_rate"] - ask["funding_rate"]
            if spread <= 0:
                continue

            rows.append({
                "symbol":           symbol,
                "strategy":         "ff",
                "exchange_bid":     bid["exchange"],
                "exchange_ask":     ask["exchange"],
                "funding_rate_bid": bid["funding_rate"],
                "funding_rate_ask": ask["funding_rate"],
                "spread":           round(spread * 100, 10),
            })

    rows.sort(key=lambda x: x["spread"], reverse=True)
    print(f"[strategies] FF pairs: {len(rows)}")
    return rows


# ─── SF spot fetchers ─────────────────────────────────────────────────────────

async def _spot_binance(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.binance.com/api/v3/exchangeInfo"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        s["baseAsset"].upper()
        for s in data.get("symbols", [])
        if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
    }


async def _spot_bingx(session: aiohttp.ClientSession) -> set[str]:
    url = "https://open-api.bingx.com/openApi/spot/v1/common/symbols"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    result = set()
    for item in data.get("data", {}).get("symbols", []):
        if item.get("status") != 1:
            continue
        sym = item.get("symbol", "")
        if sym.endswith("-USDT"):
            result.add(sym[:-5].upper())
    return result


async def _spot_bybit(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.bybit.com/v5/market/instruments-info?category=spot"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["baseCoin"].upper()
        for i in data.get("result", {}).get("list", [])
        if i.get("status") == "Trading" and i.get("quoteCoin") == "USDT"
    }


async def _spot_bitget(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.bitget.com/api/v2/spot/public/symbols"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["baseCoin"].upper()
        for i in data.get("data", [])
        if i.get("status") == "online" and i.get("quoteCoin") == "USDT"
    }


async def _spot_gate(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.gateio.ws/api/v4/spot/currency_pairs"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        item["base"].upper()
        for item in data
        if item.get("trade_status") == "tradable" and item.get("quote") == "USDT"
    }


async def _spot_kucoin(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.kucoin.com/api/v2/symbols"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["baseCurrency"].upper()
        for i in data.get("data", [])
        if i.get("enableTrading") is True and i.get("quoteCurrency") == "USDT"
    }


async def _spot_mexc(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.mexc.com/api/v3/exchangeInfo"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        s["baseAsset"].upper()
        for s in data.get("symbols", [])
        if s.get("status") == "1"
        and s.get("quoteAsset") == "USDT"
        and s.get("isSpotTradingAllowed") is True
    }


async def _spot_okx(session: aiohttp.ClientSession) -> set[str]:
    url = "https://www.okx.com/api/v5/public/instruments?instType=SPOT"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["baseCcy"].upper()
        for i in data.get("data", [])
        if i.get("state") == "live" and i.get("quoteCcy") == "USDT"
    }


SPOT_FETCHERS = {
    "Binance": _spot_binance,
    "BingX":   _spot_bingx,
    "Bybit":   _spot_bybit,
    "Bitget":  _spot_bitget,
    "Gate.io": _spot_gate,
    "KuCoin":  _spot_kucoin,
    "MEXC":    _spot_mexc,
    "OKX":     _spot_okx,
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


async def build_sf_list(records: list[dict], session: aiohttp.ClientSession) -> list[dict]:
    """
    Build Spot/Futures list.
    Only positive funding rates. Enriches with available spot exchanges.
    Returns list sorted by funding_rate descending.
    """
    sf_candidates = [r for r in records if r["funding_rate"] > 0]
    print(f"[strategies] SF candidates (positive funding): {len(sf_candidates)}")
    print("[strategies] Fetching spot exchange lists...")

    gathered = await asyncio.gather(
        *[fetcher(session) for fetcher in SPOT_FETCHERS.values()],
        return_exceptions=True,
    )

    spot_bases: dict[str, set[str] | None] = {}
    for exchange, result in zip(SPOT_FETCHERS.keys(), gathered):
        if isinstance(result, Exception):
            print(f"  x {exchange:10s}  ERROR — {result}")
            spot_bases[exchange] = None
        else:
            spot_bases[exchange] = result
            print(f"  v {exchange:10s}  {len(result):4d} USDT spot pairs")

    exchange_order = list(SPOT_FETCHERS.keys())
    rows, dropped = [], 0

    for rec in sf_candidates:
        futures_exchange = rec["exchange"]
        symbol           = rec["symbol"]
        funding_rate     = rec["funding_rate"]
        base             = base_from_futures(symbol, futures_exchange).upper()

        ask_exchanges = [
            ex for ex in exchange_order
            if spot_bases.get(ex) is not None and base in spot_bases[ex]
        ]

        if not ask_exchanges:
            dropped += 1
            continue

        row: dict = {
            "symbol":       symbol,
            "strategy":     "sf",
            "exchange_bid": futures_exchange,
            "funding_rate": funding_rate,
            "spread":       round(funding_rate * 100, 10),
        }
        for idx, ex in enumerate(ask_exchanges, start=1):
            row[f"exchange_ask_{idx}"] = ex

        rows.append(row)

    rows.sort(key=lambda x: x["funding_rate"], reverse=True)
    print(f"  SF kept: {len(rows)} | dropped (no spot): {dropped}\n")
    return rows
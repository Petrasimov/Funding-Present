"""
core/fetchers.py — Fetch current funding rates from all 8 exchanges.

Each fetcher returns a list of dicts:
  { "symbol": str, "exchange": str, "funding_rate": float }

Main entry point: fetch_all(session) -> list[dict]
"""

import asyncio
import aiohttp
from datetime import datetime

from core.utils import fmt, valid


# ─── Per-exchange fetchers ────────────────────────────────────────────────────

async def fetch_binance(session: aiohttp.ClientSession) -> list[dict]:
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data:
        try:
            funding_str = item.get("lastFundingRate", "")
            if not funding_str:
                continue
            if not item["symbol"].endswith("USDT"):
                continue
            rate = fmt(float(funding_str))
            if not valid(rate):
                continue
            results.append({"symbol": item["symbol"], "exchange": "Binance", "funding_rate": rate})
        except (ValueError, KeyError):
            pass
    return results


async def fetch_bingx(session: aiohttp.ClientSession) -> list[dict]:
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data.get("data", []):
        try:
            funding_str = item.get("lastFundingRate", "")
            if not funding_str:
                continue
            if not item["symbol"].endswith("-USDT"):
                continue
            rate = fmt(float(funding_str))
            if not valid(rate):
                continue
            results.append({"symbol": item["symbol"], "exchange": "BingX", "funding_rate": rate})
        except (ValueError, KeyError):
            pass
    return results


async def fetch_bitget(session: aiohttp.ClientSession) -> list[dict]:
    url = "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data.get("data", []):
        try:
            funding = item.get("fundingRate")
            if funding is None:
                continue
            if not item["symbol"].endswith("USDT"):
                continue
            rate = fmt(float(funding))
            if not valid(rate):
                continue
            results.append({"symbol": item["symbol"], "exchange": "Bitget", "funding_rate": rate})
        except (ValueError, KeyError):
            pass
    return results


async def fetch_bybit(session: aiohttp.ClientSession) -> list[dict]:
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data.get("result", {}).get("list", []):
        try:
            funding = item.get("fundingRate")
            if funding is None:
                continue
            if not item["symbol"].endswith("USDT"):
                continue
            rate = fmt(float(funding))
            if not valid(rate):
                continue
            results.append({"symbol": item["symbol"], "exchange": "Bybit", "funding_rate": rate})
        except (ValueError, KeyError):
            pass
    return results


async def fetch_gate(session: aiohttp.ClientSession) -> list[dict]:
    url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data:
        try:
            funding = item.get("funding_rate")
            if funding is None:
                continue
            symbol = item.get("contract", "")
            if not symbol.endswith("_USDT"):
                continue
            rate = fmt(float(funding))
            if not valid(rate):
                continue
            results.append({"symbol": symbol, "exchange": "Gate.io", "funding_rate": rate})
        except (ValueError, KeyError):
            pass
    return results


async def fetch_kucoin(session: aiohttp.ClientSession) -> list[dict]:
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data.get("data", []):
        try:
            funding = item.get("fundingFeeRate")
            if funding is None:
                continue
            if not item["symbol"].endswith("USDTM"):
                continue
            rate = fmt(float(funding))
            if not valid(rate):
                continue
            results.append({"symbol": item["symbol"], "exchange": "KuCoin", "funding_rate": rate})
        except (ValueError, KeyError):
            pass
    return results


async def fetch_mexc(session: aiohttp.ClientSession) -> list[dict]:
    url = "https://contract.mexc.com/api/v1/contract/ticker"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data.get("data", []):
        try:
            funding = item.get("fundingRate")
            if funding is None:
                continue
            if not item["symbol"].endswith("_USDT"):
                continue
            rate = fmt(float(funding))
            if not valid(rate):
                continue
            results.append({"symbol": item["symbol"], "exchange": "MEXC", "funding_rate": rate})
        except (ValueError, KeyError):
            pass
    return results


async def _okx_get_swap_instruments(session: aiohttp.ClientSession) -> list[str]:
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return [
        item["instId"]
        for item in data.get("data", [])
        if item.get("settleCcy") == "USDT"
    ]


async def _okx_fetch_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    inst_id: str,
) -> dict | None:
    async with sem:
        url = f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            items = data.get("data", [])
            if not items:
                return None
            funding_str = items[0].get("fundingRate", "")
            if not funding_str:
                return None
            rate = fmt(float(funding_str))
            if not valid(rate):
                return None
            return {"symbol": inst_id, "exchange": "OKX", "funding_rate": rate}
        except Exception:
            return None


async def fetch_okx(session: aiohttp.ClientSession) -> list[dict]:
    instruments = await _okx_get_swap_instruments(session)
    sem = asyncio.Semaphore(30)
    tasks = [_okx_fetch_one(session, sem, inst_id) for inst_id in instruments]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


# ─── Main entry point ─────────────────────────────────────────────────────────

EXCHANGE_FETCHERS = {
    "Binance": fetch_binance,
    "BingX":   fetch_bingx,
    "Bitget":  fetch_bitget,
    "Bybit":   fetch_bybit,
    "Gate.io": fetch_gate,
    "KuCoin":  fetch_kucoin,
    "MEXC":    fetch_mexc,
    "OKX":     fetch_okx,
}


async def fetch_all(session: aiohttp.ClientSession) -> list[dict]:
    """
    Fetch funding rates from all 8 exchanges concurrently.
    Returns merged list, USDC-base symbols removed.
    """
    print(f"[fetchers] Fetching funding rates — {datetime.utcnow().strftime('%H:%M:%S')} UTC")

    gathered = await asyncio.gather(
        *[fetcher(session) for fetcher in EXCHANGE_FETCHERS.values()],
        return_exceptions=True,
    )

    all_results: list[dict] = []
    for name, result in zip(EXCHANGE_FETCHERS.keys(), gathered):
        if isinstance(result, Exception):
            print(f"  x {name:10s}  ERROR — {result}")
        else:
            all_results.extend(result)
            print(f"  v {name:10s}  {len(result):4d} symbols")

    # Remove USDC-base symbols
    before = len(all_results)
    all_results = [r for r in all_results if not r["symbol"].upper().startswith("USDC")]
    dropped = before - len(all_results)
    if dropped:
        print(f"  (dropped {dropped} USDC-base symbols)")

    print(f"  Total: {len(all_results)} records\n")
    return all_results
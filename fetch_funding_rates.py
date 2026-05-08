"""
Fetches current funding rates from 8 exchanges and saves to funding_rates.json
Output format: [{"symbol": "BTCUSDT", "exchange": "Binance", "funding_rate": 0.0001}, ...]
"""

import asyncio
import aiohttp
import json
import re
from decimal import Decimal
from datetime import datetime


def fmt(v: float) -> float:
    """Round-trip through Decimal for clean representation; skip in callers if zero."""
    return float(Decimal(repr(v)))


_EXCLUDED = {0.0, 0.00005, -0.00005}

def valid(v: float) -> bool:
    """Return True if funding rate is a real value, not a zero or exchange default."""
    return v not in _EXCLUDED


def dump_decimal(obj) -> str:
    """json.dumps with floats in decimal notation, never scientific."""
    raw = json.dumps(obj, indent=2, ensure_ascii=False)

    def fix(m: re.Match) -> str:
        return format(Decimal(m.group(0)), 'f')

    return re.sub(r'-?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', fix, raw)


# ─── Exchange fetchers ────────────────────────────────────────────────────────

async def fetch_binance(session: aiohttp.ClientSession) -> list[dict]:
    """
    /fapi/v1/fundingRate requires a symbol param (historical data).
    /fapi/v1/premiumIndex returns current funding rates for all symbols.
    """
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data:
        try:
            funding_str = item.get("lastFundingRate", "")
            if funding_str == "" or funding_str is None:
                continue
            if not item["symbol"].endswith("USDT"):
                continue
            rate = fmt(float(funding_str))
            if not valid(rate):
                continue
            results.append({
                "symbol": item["symbol"],
                "exchange": "Binance",
                "funding_rate": rate,
            })
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
            if funding_str == "" or funding_str is None:
                continue
            if not item["symbol"].endswith("-USDT"):
                continue
            rate = fmt(float(funding_str))
            if not valid(rate):
                continue
            results.append({
                "symbol": item["symbol"],
                "exchange": "BingX",
                "funding_rate": rate,
            })
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
            funding_str = item.get("fundingRate", "")
            if funding_str == "" or funding_str is None:
                continue
            rate = fmt(float(funding_str))
            if not valid(rate):
                continue
            results.append({
                "symbol": item["symbol"],
                "exchange": "Bitget",
                "funding_rate": rate,
            })
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
            funding_str = item.get("fundingRate", "")
            if funding_str == "" or funding_str is None:
                continue
            sym = item["symbol"]
            if not (sym.endswith("USDT") and not sym.endswith("PERP")):
                continue
            rate = fmt(float(funding_str))
            if not valid(rate):
                continue
            results.append({
                "symbol": sym,
                "exchange": "Bybit",
                "funding_rate": rate,
            })
        except (ValueError, KeyError):
            pass
    return results


async def fetch_gateio(session: aiohttp.ClientSession) -> list[dict]:
    url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results = []
    for item in data:
        try:
            funding_str = item.get("funding_rate", "")
            if funding_str == "" or funding_str is None:
                continue
            contract = item["contract"]
            if not contract.endswith("_USDT"):
                continue
            rate = fmt(float(funding_str))
            if not valid(rate):
                continue
            results.append({
                "symbol": contract,
                "exchange": "Gate.io",
                "funding_rate": rate,
            })
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
            results.append({
                "symbol": item["symbol"],
                "exchange": "KuCoin",
                "funding_rate": rate,
            })
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
            results.append({
                "symbol": item["symbol"],
                "exchange": "MEXC",
                "funding_rate": rate,
            })
        except (ValueError, KeyError):
            pass
    return results


async def _okx_get_swap_instruments(session: aiohttp.ClientSession) -> list[str]:
    """Returns instIds of all USDT-margined perpetual swaps on OKX."""
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
            if funding_str == "" or funding_str is None:
                return None
            rate = fmt(float(funding_str))
            if not valid(rate):
                return None
            return {
                "symbol": inst_id,
                "exchange": "OKX",
                "funding_rate": rate,
            }
        except Exception:
            return None


async def fetch_okx(session: aiohttp.ClientSession) -> list[dict]:
    """
    OKX funding-rate endpoint requires instId.
    We first list all USDT SWAP instruments, then fetch each concurrently.
    """
    instruments = await _okx_get_swap_instruments(session)
    sem = asyncio.Semaphore(30)  # max 30 concurrent requests
    tasks = [_okx_fetch_one(session, sem, inst_id) for inst_id in instruments]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


# ─── Main ─────────────────────────────────────────────────────────────────────

EXCHANGES = {
    "Binance": fetch_binance,
    "BingX": fetch_bingx,
    "Bitget": fetch_bitget,
    "Bybit": fetch_bybit,
    "Gate.io": fetch_gateio,
    "KuCoin": fetch_kucoin,
    "MEXC": fetch_mexc,
    "OKX": fetch_okx,
}


async def main() -> None:
    print(f"Fetching funding rates — {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    timeout = aiohttp.ClientTimeout(total=60)
    connector = aiohttp.TCPConnector(limit=100)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = {name: fetcher(session) for name, fetcher in EXCHANGES.items()}

        # Run all exchanges concurrently
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

    all_results: list[dict] = []
    for name, result in zip(tasks.keys(), gathered):
        if isinstance(result, Exception):
            print(f"  x {name:10s}  ERROR — {result}")
        else:
            all_results.extend(result)
            print(f"  v {name:10s}  {len(result):4d} symbols")

    # Drop any symbol whose BASE coin is USDC (e.g. USDC_USDT, USDCUSDT, USDCUSDTM)
    before = len(all_results)
    all_results = [r for r in all_results if not r["symbol"].upper().startswith("USDC")]
    dropped_stables = before - len(all_results)
    if dropped_stables:
        print(f"  (dropped {dropped_stables} USDC-base symbols)")

    output_file = "funding_rates.json"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(dump_decimal(all_results))

    print(f"\nTotal: {len(all_results)} records -> {output_file}")


if __name__ == "__main__":
    asyncio.run(main())

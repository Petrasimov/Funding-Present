"""
Reads funding_rates.json and removes any symbol that is not currently
active for trading on its exchange.

Steps:
  1. Load funding_rates.json
  2. Fetch active contract sets from each exchange (async, concurrent)
  3. Keep only entries whose (exchange, symbol) pair is in the active set
  4. Write result to funding_rates_v2.json

Active-status logic per exchange
  Binance  — exchangeInfo  -> symbols[].status == "TRADING"
  BingX    — contracts     -> data[].status == 1
  Bitget   — contracts     -> data[].status == "normal"
  Bybit    — instruments   -> result.list[].status == "Trading"
  Gate.io  — contracts     -> in_delisting == false
  KuCoin   — /active       -> all returned are active
  MEXC     — detail        -> data[].state == 0
  OKX      — instruments   -> data[].state == "live", settleCcy == "USDT"
"""

import asyncio
import aiohttp
import json
import re
from decimal import Decimal
from pathlib import Path


# --- Decimal formatting (same as fetch_funding_rates.py) ---------------------

def dump_decimal(obj) -> str:
    """json.dumps with floats rendered in decimal notation, never scientific."""
    raw = json.dumps(obj, indent=2, ensure_ascii=False)

    def fix(m: re.Match) -> str:
        return format(Decimal(m.group(0)), 'f')

    return re.sub(r'-?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', fix, raw)


# --- Active-symbol fetchers ---------------------------------------------------

async def active_binance(session: aiohttp.ClientSession) -> set[str]:
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        s["symbol"]
        for s in data.get("symbols", [])
        if s.get("status") == "TRADING"
    }


async def active_bingx(session: aiohttp.ClientSession) -> set[str]:
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/contracts"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    # status == 1  ->  online / active
    return {
        c["symbol"]
        for c in data.get("data", [])
        if c.get("status") == 1
    }


async def active_bitget(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        c["symbol"]
        for c in data.get("data", [])
        if c.get("symbolStatus") == "normal"
    }


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


async def active_gateio(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    # in_delisting == False means the contract is still tradeable
    return {
        c["name"]
        for c in data
        if not c.get("in_delisting", False)
    }


async def active_kucoin(session: aiohttp.ClientSession) -> set[str]:
    # Endpoint already returns only active contracts
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
    # state == 0  ->  enabled / normal trading
    return {
        c["symbol"]
        for c in data.get("data", [])
        if c.get("state") == 0
    }


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


# exchange name (as stored in funding_rates.json) -> fetcher
ACTIVE_FETCHERS: dict[str, object] = {
    "Binance": active_binance,
    "BingX":   active_bingx,
    "Bitget":  active_bitget,
    "Bybit":   active_bybit,
    "Gate.io": active_gateio,
    "KuCoin":  active_kucoin,
    "MEXC":    active_mexc,
    "OKX":     active_okx,
}


# --- Main ---------------------------------------------------------------------

async def main() -> None:
    input_file = Path("funding_rates.json")
    output_file = Path("funding_rates_v2.json")

    if not input_file.exists():
        print(f"ERROR: {input_file} not found. Run fetch_funding_rates.py first.")
        return

    with input_file.open(encoding="utf-8") as f:
        funding_rates: list[dict] = json.load(f)

    print(f"Loaded {len(funding_rates)} records from {input_file}\n")
    print("Fetching active contract lists from all exchanges...")

    timeout = aiohttp.ClientTimeout(total=60)
    connector = aiohttp.TCPConnector(limit=50)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
        gathered = await asyncio.gather(
            *[fetcher(session) for fetcher in ACTIVE_FETCHERS.values()],
            return_exceptions=True,
        )

    # Build  { "Binance": {"BTCUSDT", ...}, "BingX": {...}, ... }
    active_sets: dict[str, set[str]] = {}
    for exchange, result in zip(ACTIVE_FETCHERS.keys(), gathered):
        if isinstance(result, Exception):
            print(f"  x {exchange:10s}  ERROR — {result}")
            # Keep all symbols for this exchange so we don't wrongly drop them
            active_sets[exchange] = None
        else:
            active_sets[exchange] = result
            print(f"  v {exchange:10s}  {len(result):4d} active contracts")

    # Filter
    filtered: list[dict] = []
    dropped = 0
    for entry in funding_rates:
        exchange = entry.get("exchange", "")
        symbol   = entry.get("symbol", "")
        active   = active_sets.get(exchange)
        if active is None:
            # Fetch failed — keep the record (safe fallback)
            filtered.append(entry)
        elif symbol in active:
            filtered.append(entry)
        else:
            dropped += 1

    with output_file.open("w", encoding="utf-8") as f:
        f.write(dump_decimal(filtered))

    print(f"\nRemoved : {dropped} inactive records")
    print(f"Kept    : {len(filtered)} active records")
    print(f"Saved   -> {output_file}")


if __name__ == "__main__":
    asyncio.run(main())

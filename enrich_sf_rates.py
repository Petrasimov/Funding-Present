"""
Enriches sf_rates_v_1.json with spot exchange availability.

Steps:
  1. Fetch USDT spot pairs from 8 exchanges (async, concurrent)
  2. For each futures record, extract base coin and find which spot
     exchanges also list that coin as a USDT pair
  3. Drop records with zero spot exchanges found
  4. Save result to sf_rates_v_2.json

Active-status logic per spot exchange:
  Binance  — exchangeInfo -> symbols[].status == "TRADING",   quoteAsset == "USDT"
  BingX    — symbols      -> status == 1 (online),            quote extracted from symbol "-USDT"
  Bybit    — instruments  -> status == "Trading",             quoteCoin == "USDT"
  Bitget   — symbols      -> status == "online",              quoteCoin == "USDT"
  Gate.io  — currency_pairs -> trade_status == "tradable",    quote == "USDT"
  KuCoin   — symbols      -> enableTrading == true,           quoteCurrency == "USDT"
  MEXC     — exchangeInfo -> status == "1",                   quoteAsset == "USDT"
  OKX      — instruments  -> state == "live",                 quoteCcy == "USDT"
"""

import asyncio
import aiohttp
import json
import re
from decimal import Decimal
from pathlib import Path


# --- Helpers -----------------------------------------------------------------

def dump_decimal(obj) -> str:
    raw = json.dumps(obj, indent=2, ensure_ascii=False)
    def fix(m: re.Match) -> str:
        return format(Decimal(m.group(0)), 'f')
    return re.sub(r'-?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', fix, raw)


def base_from_futures(symbol: str, exchange: str) -> str:
    """Extract base coin (e.g. 'BTC') from a futures symbol."""
    if exchange == "KuCoin":
        # BTCUSDTM -> BTC
        if symbol.endswith("USDTM"):
            return symbol[:-5]
        if symbol.endswith("USDM"):
            return symbol[:-4]
    elif exchange == "OKX":
        # BTC-USDT-SWAP -> BTC
        return symbol.split("-")[0]
    elif exchange in ("Gate.io", "MEXC"):
        # BTC_USDT -> BTC
        return symbol.split("_")[0]
    elif exchange == "BingX":
        # BTC-USDT -> BTC
        return symbol.split("-")[0]
    elif exchange in ("Binance", "Bybit", "Bitget"):
        # BTCUSDT -> BTC
        if symbol.endswith("USDT"):
            return symbol[:-4]
    return symbol


# --- Spot active-base-coin fetchers ------------------------------------------
# Each returns a set of uppercase base coin names that are actively
# tradeable as a USDT pair on the spot market.

async def spot_binance(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.binance.com/api/v3/exchangeInfo"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        s["baseAsset"].upper()
        for s in data.get("symbols", [])
        if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
    }


async def spot_bingx(session: aiohttp.ClientSession) -> set[str]:
    url = "https://open-api.bingx.com/openApi/spot/v1/common/symbols"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    items = data.get("data", {}).get("symbols", [])
    result = set()
    for item in items:
        # status == 1 means online/active
        if item.get("status") != 1:
            continue
        sym = item.get("symbol", "")
        if sym.endswith("-USDT"):
            result.add(sym[:-5].upper())
    return result


async def spot_bybit(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.bybit.com/v5/market/instruments-info?category=spot"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["baseCoin"].upper()
        for i in data.get("result", {}).get("list", [])
        if i.get("status") == "Trading" and i.get("quoteCoin") == "USDT"
    }


async def spot_bitget(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.bitget.com/api/v2/spot/public/symbols"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["baseCoin"].upper()
        for i in data.get("data", [])
        if i.get("status") == "online" and i.get("quoteCoin") == "USDT"
    }


async def spot_gateio(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.gateio.ws/api/v4/spot/currency_pairs"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        item["base"].upper()
        for item in data
        if item.get("trade_status") == "tradable" and item.get("quote") == "USDT"
    }


async def spot_kucoin(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.kucoin.com/api/v2/symbols"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["baseCurrency"].upper()
        for i in data.get("data", [])
        if i.get("enableTrading") is True and i.get("quoteCurrency") == "USDT"
    }


async def spot_mexc(session: aiohttp.ClientSession) -> set[str]:
    url = "https://api.mexc.com/api/v3/exchangeInfo"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        s["baseAsset"].upper()
        for s in data.get("symbols", [])
        if s.get("status") == "1" and s.get("quoteAsset") == "USDT"
        and s.get("isSpotTradingAllowed") is True
    }


async def spot_okx(session: aiohttp.ClientSession) -> set[str]:
    url = "https://www.okx.com/api/v5/public/instruments?instType=SPOT"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return {
        i["baseCcy"].upper()
        for i in data.get("data", [])
        if i.get("state") == "live" and i.get("quoteCcy") == "USDT"
    }


SPOT_FETCHERS: dict[str, object] = {
    "Binance": spot_binance,
    "BingX":   spot_bingx,
    "Bybit":   spot_bybit,
    "Bitget":  spot_bitget,
    "Gate.io": spot_gateio,
    "KuCoin":  spot_kucoin,
    "MEXC":    spot_mexc,
    "OKX":     spot_okx,
}


# --- Main ---------------------------------------------------------------------

async def main() -> None:
    input_file = Path("sf_rates_v_1.json")
    output_file = Path("sf_rates_v_2.json")

    if not input_file.exists():
        print(f"ERROR: {input_file} not found.")
        return

    with input_file.open(encoding="utf-8") as f:
        sf_records: list[dict] = json.load(f)

    print(f"Loaded {len(sf_records)} records from {input_file}\n")
    print("Fetching active spot pairs from all exchanges...")

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
            *[fetcher(session) for fetcher in SPOT_FETCHERS.values()],
            return_exceptions=True,
        )

    # { "Binance": {"BTC", "ETH", ...}, ... }
    spot_bases: dict[str, set[str] | None] = {}
    for exchange, result in zip(SPOT_FETCHERS.keys(), gathered):
        if isinstance(result, Exception):
            print(f"  x {exchange:8s}  ERROR - {result}")
            spot_bases[exchange] = None
        else:
            spot_bases[exchange] = result
            print(f"  v {exchange:8s}  {len(result):4d} USDT spot pairs")

    # All exchange names in a consistent order for exchange_ask columns
    exchange_order = list(SPOT_FETCHERS.keys())

    print("\nBuilding sf_rates_v_2.json ...")
    result_rows: list[dict] = []
    dropped = 0

    for rec in sf_records:
        futures_exchange = rec["exchange"]
        symbol = rec["symbol"]
        funding_rate = rec["funding_rate"]

        base = base_from_futures(symbol, futures_exchange).upper()

        # Find all spot exchanges that have this base coin as USDT pair
        ask_exchanges: list[str] = []
        for ex in exchange_order:
            bases = spot_bases.get(ex)
            if bases is None:
                continue  # fetch failed, skip
            if base in bases:
                ask_exchanges.append(ex)

        if not ask_exchanges:
            dropped += 1
            continue

        row: dict = {
            "symbol":       symbol,
            "exchange_bid": futures_exchange,
        }
        for idx, ex in enumerate(ask_exchanges, start=1):
            row[f"exchange_ask_{idx}"] = ex
        row["funding_rate"] = funding_rate
        row["spread"] = round(funding_rate * 100, 10)

        result_rows.append(row)

    with output_file.open("w", encoding="utf-8") as f:
        f.write(dump_decimal(result_rows))

    print(f"\nKept    : {len(result_rows)} records")
    print(f"Dropped : {dropped} (no spot exchange found)")
    print(f"Saved   -> {output_file}")

    # Preview
    print("\n-- Sample output (first 3) --")
    for row in result_rows[:3]:
        ask_cols = {k: v for k, v in row.items() if k.startswith("exchange_ask")}
        print(f"  {row['symbol']:20s}  bid={row['exchange_bid']:8s}  "
              f"ask={list(ask_cols.values())}  rate={row['funding_rate']}")


if __name__ == "__main__":
    asyncio.run(main())

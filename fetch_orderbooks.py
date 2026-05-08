"""
Fetches orderbooks for every record in funding_rates_v3.json and writes
funding_rates_v4.json.

ff records  → 2 futures orderbook requests (exchange_ask → _a, exchange_bid → _b)
sf records  → 1 futures orderbook request  (exchange_bid → _b)
              + N spot orderbook requests   (exchange_ask_N → _a_N)

Deduplication: all records are pre-scanned to collect unique
(base_coin, exchange, market) triples. Each triple is fetched exactly once;
results are shared across all records that reference the same triple.

All bids/asks are stored as [[price, qty], ...] (first 2 elements only).
"""

import asyncio
import aiohttp
import json
import re
import sys
import time
from collections import deque
from decimal import Decimal
from pathlib import Path

# Force UTF-8 output so non-ASCII symbols print correctly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


LIMIT = 20

# Per-exchange concurrency cap to avoid rate-limits
_SEMS: dict[str, asyncio.Semaphore] = {}

def _sem(exchange: str) -> asyncio.Semaphore:
    if exchange not in _SEMS:
        size = _EXCHANGE_SEM_SIZES.get(exchange, _DEFAULT_SEM_SIZE)
        _SEMS[exchange] = asyncio.Semaphore(size)
    return _SEMS[exchange]

# Gate.io blocks Chrome UA → use default aiohttp UA
# MEXC blocks default aiohttp UA → use Chrome UA
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_EXCHANGE_HEADERS: dict[str, dict] = {
    "MEXC": {"User-Agent": _CHROME_UA, "Accept": "application/json"},
}

# Per-exchange concurrency caps (semaphore slots)
# Higher = more parallel requests; bounded by the rate limiter below.
_EXCHANGE_SEM_SIZES: dict[str, int] = {
    "KuCoin": 8,
    "OKX":    8,
}
_DEFAULT_SEM_SIZE = 10

# ─── Per-exchange rate limits (max requests per second) ──────────────────────
# Set to ~75-80 % of each exchange's published public-endpoint limit so we
# stay clearly below the threshold even with occasional bursts.
#
#   Binance  depth=20 → 5 weight; hard cap 2400 weight/min → 480/min → 8/s
#   BingX    public swap market data: 200 req/10 s → 20/s; use 12
#   Bitget   public market data: 20 req/s; use 15
#   Bybit    public orderbook: 600 req/5 s → 120/s; use 20
#   Gate.io  public: 300 req/s; use 20
#   KuCoin   public: 30 req/3 s = 10/s; use 8
#   MEXC     soft-limits aggressively; 8/s (same as others, retry handles empties)
#   OKX      public: 20 req/2 s = 10/s; use 8
_RATE_LIMITS: dict[str, float] = {
    "Binance": 8,
    "BingX":   12,
    "Bitget":  15,
    "Bybit":   20,
    "Gate.io": 20,
    "KuCoin":  8,
    "MEXC":    8,
    "OKX":     8,
}
_DEFAULT_RATE: float = 8.0  # req/s fallback


class _RateLimiter:
    """
    Sliding-window per-exchange rate limiter.

    Tracks the last N request timestamps per exchange (N = rate limit).
    If the 1-second window is already full, sleeps until the oldest
    timestamp exits the window before allowing the next request.
    asyncio.Lock ensures correctness under concurrent coroutines.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._times: dict[str, deque] = {}

    def _ensure(self, exchange: str) -> tuple:
        if exchange not in self._locks:
            self._locks[exchange] = asyncio.Lock()
            self._times[exchange] = deque()
        return self._locks[exchange], self._times[exchange]

    async def wait(self, exchange: str) -> None:
        lock, times = self._ensure(exchange)
        limit = _RATE_LIMITS.get(exchange, _DEFAULT_RATE)
        async with lock:
            now = time.monotonic()
            # Remove timestamps older than 1 second
            while times and now - times[0] >= 1.0:
                times.popleft()
            if len(times) >= limit:
                # Sleep until the oldest timestamp exits the window
                sleep_for = 1.0 - (now - times[0]) + 0.001
                await asyncio.sleep(sleep_for)
                now = time.monotonic()
                while times and now - times[0] >= 1.0:
                    times.popleft()
            times.append(time.monotonic())


_RATE_LIMITER = _RateLimiter()


# ─── Symbol helpers ───────────────────────────────────────────────────────────

def base_from_futures(symbol: str, exchange: str) -> str:
    if exchange == "KuCoin":
        if symbol.endswith("USDTM"): return symbol[:-5]
        if symbol.endswith("USDM"):  return symbol[:-4]
    elif exchange == "OKX":
        return symbol.split("-")[0]
    elif exchange in ("Gate.io", "MEXC"):
        return symbol.split("_")[0]
    elif exchange == "BingX":
        return symbol.split("-")[0]
    elif symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


# ─── URL builders ─────────────────────────────────────────────────────────────

def futures_url(symbol: str, exchange: str) -> str:
    """
    Build futures orderbook URL using the ORIGINAL symbol from the exchange
    (not a reconstructed base+USDT), so USDC-margined and other non-USDT
    contracts (e.g. MEXC FIL_USDC, TON_USDC) are fetched correctly.
    """
    if exchange == "Binance":
        # symbol: BTCUSDT
        return f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit={LIMIT}"
    if exchange == "BingX":
        # symbol: BTC-USDT
        return f"https://open-api.bingx.com/openApi/swap/v2/quote/depth?symbol={symbol}&limit={LIMIT}"
    if exchange == "Bitget":
        # symbol: BTCUSDT — detect product type from quote currency
        pt = "usdc-futures" if symbol.endswith("USDC") else "usdt-futures"
        return f"https://api.bitget.com/api/v2/mix/market/merge-depth?symbol={symbol}&productType={pt}&limit={LIMIT}"
    if exchange == "Gate.io":
        # symbol: BTC_USDT
        return f"https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={symbol}&limit={LIMIT}"
    if exchange == "Bybit":
        # symbol: BTCUSDT
        return f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit={LIMIT}"
    if exchange == "KuCoin":
        # symbol: XBTUSDTM
        return f"https://api-futures.kucoin.com/api/v1/level2/snapshot?symbol={symbol}"
    if exchange == "MEXC":
        # symbol: BTC_USDT or FIL_USDC — use as-is
        return f"https://contract.mexc.com/api/v1/contract/depth/{symbol}"
    if exchange == "OKX":
        # symbol: BTC-USDT-SWAP
        return f"https://www.okx.com/api/v5/market/books?instId={symbol}&sz={LIMIT}"
    return ""


def spot_url(base: str, exchange: str) -> str:
    if exchange == "Binance":
        return f"https://api.binance.com/api/v3/depth?symbol={base}USDT&limit={LIMIT}"
    if exchange == "BingX":
        return f"https://open-api.bingx.com/openApi/spot/v2/market/depth?symbol={base}-USDT&depth={LIMIT}&type=step0"
    if exchange == "Bitget":
        return f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={base}USDT&limit={LIMIT}"
    if exchange == "Bybit":
        return f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={base}USDT&limit={LIMIT}"
    if exchange == "Gate.io":
        return f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={base}_USDT&limit={LIMIT}"
    if exchange == "KuCoin":
        return f"https://api.kucoin.com/api/v1/market/orderbook/level2_20?symbol={base}-USDT"
    if exchange == "MEXC":
        return f"https://api.mexc.com/api/v3/depth?symbol={base}USDT&limit={LIMIT}"
    if exchange == "OKX":
        return f"https://www.okx.com/api/v5/market/books?instId={base}-USDT&sz={LIMIT}"
    return ""


# ─── Response parser ──────────────────────────────────────────────────────────

def _take2(row) -> list:
    if isinstance(row, dict):            # Gate.io futures: {"p": price, "s": size}
        return [row.get("p", ""), row.get("s", "")]
    if isinstance(row, (list, tuple)):
        return [row[0], row[1]]
    return row


def parse_ob(data: dict, exchange: str, market: str) -> tuple[list, list]:
    try:
        if exchange in ("Binance", "MEXC") and market == "spot":
            bids, asks = data.get("bids", []), data.get("asks", [])
        elif exchange == "Binance" and market == "futures":
            bids, asks = data.get("bids", []), data.get("asks", [])
        elif exchange == "BingX":
            d = data.get("data", {})
            bids, asks = d.get("bids", []), d.get("asks", [])
        elif exchange == "Bitget":
            d = data.get("data", {})
            bids, asks = d.get("bids", []), d.get("asks", [])
        elif exchange == "Bybit":
            r = data.get("result", {})
            bids, asks = r.get("b", []), r.get("a", [])
        elif exchange == "Gate.io":
            bids, asks = data.get("bids", []), data.get("asks", [])
        elif exchange == "KuCoin":
            d = data.get("data", {})
            bids, asks = d.get("bids", []), d.get("asks", [])
        elif exchange == "MEXC" and market == "futures":
            d = data.get("data", {})
            bids, asks = d.get("bids", []), d.get("asks", [])
        elif exchange == "OKX":
            d = (data.get("data") or [{}])[0]
            bids, asks = d.get("bids", []), d.get("asks", [])
        else:
            bids, asks = [], []
        return [_take2(r) for r in bids], [_take2(r) for r in asks]
    except Exception:
        return [], []


# ─── Single fetch (used for each unique key) ─────────────────────────────────

_done_count = 0
_total_count = 0
_t_start = 0.0

async def _fetch_one(
    session: aiohttp.ClientSession,
    key: tuple,                          # (sym_or_base, exchange, market)
) -> tuple[tuple, tuple[list, list]]:
    global _done_count
    sym, exchange, market = key
    url = futures_url(sym, exchange) if market == "futures" else spot_url(sym, exchange)
    if not url:
        return key, ([], [])
    headers = _EXCHANGE_HEADERS.get(exchange)

    t0 = time.monotonic()
    status_str = "ok"

    # retry_sleep is set when a retry is needed; sleeping happens OUTSIDE
    # the semaphore so the slot is released while waiting, letting other
    # requests proceed instead of blocking the whole pool.
    retry_sleep = 0.0
    for attempt in range(4):
        if retry_sleep > 0.0:
            await asyncio.sleep(retry_sleep)
            retry_sleep = 0.0

        async with _sem(exchange):
            await _RATE_LIMITER.wait(exchange)
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as resp:
                    if resp.status == 429:
                        status_str = f"429 retry#{attempt+1}"
                        retry_sleep = 1.0 + attempt
                        continue
                    if resp.status != 200:
                        status_str = f"HTTP {resp.status}"
                        result = ([], [])
                        break
                    data = await resp.json(content_type=None)

                result = parse_ob(data, exchange, market)

                # Empty bids = soft rate-limit (MEXC returns 200 but no data)
                if len(result[0]) == 0 and attempt < 3:
                    status_str = f"empty retry#{attempt+1}"
                    retry_sleep = 0.5 * (attempt + 1)
                    continue

                break
            except Exception as e:
                status_str = f"ERR {type(e).__name__}"
                if attempt < 3:
                    retry_sleep = 0.5 * (attempt + 1)
                    continue
                result = ([], [])
                break
    else:
        result = ([], [])

    elapsed = time.monotonic() - t0
    _done_count += 1
    elapsed_total = time.monotonic() - _t_start
    bids_len = len(result[0])
    empty_mark = " [EMPTY]" if bids_len == 0 else ""
    print(
        f"[{_done_count:4d}/{_total_count}] {elapsed_total:6.1f}s"
        f"  {exchange:8s} {market:7s}  {sym:25s}"
        f"  {elapsed:.2f}s  {status_str}{empty_mark}"
    )
    return key, result


# ─── Pre-scan: collect all unique (base, exchange, market) needed ─────────────

def collect_keys(records: list[dict]) -> set[tuple]:
    keys: set[tuple] = set()
    for rec in records:
        symbol   = rec["symbol"]
        strategy = rec.get("strategy")
        ex_bid   = rec["exchange_bid"]
        base     = base_from_futures(symbol, ex_bid)

        if strategy == "ff":
            ex_ask = rec["exchange_ask"]
            # futures keys use original symbol (preserves USDC/USDT/etc.)
            keys.add((symbol, ex_bid, "futures"))
            keys.add((symbol, ex_ask, "futures"))

        elif strategy == "sf":
            keys.add((symbol, ex_bid, "futures"))
            for k, v in rec.items():
                if k.startswith("exchange_ask_"):
                    # spot keys use base coin (always USDT on spot)
                    keys.add((base, v, "spot"))

    return keys


# ─── Build output rows from cache ────────────────────────────────────────────

def build_ff(rec: dict, cache: dict) -> dict:
    symbol = rec["symbol"]
    ex_bid = rec["exchange_bid"]
    ex_ask = rec["exchange_ask"]

    bids_a, asks_a = cache.get((symbol, ex_ask, "futures"), ([], []))
    bids_b, asks_b = cache.get((symbol, ex_bid, "futures"), ([], []))

    return {**rec,
            "ask_a": asks_a, "bid_a": bids_a,
            "ask_b": asks_b, "bid_b": bids_b}


def build_sf(rec: dict, cache: dict) -> dict:
    symbol = rec["symbol"]
    ex_bid = rec["exchange_bid"]
    base   = base_from_futures(symbol, ex_bid)

    bids_b, asks_b = cache.get((symbol, ex_bid, "futures"), ([], []))
    row = {**rec, "ask_b": asks_b, "bid_b": bids_b}

    ask_keys = sorted(
        [k for k in rec if k.startswith("exchange_ask_")],
        key=lambda k: int(k.split("_")[-1]),
    )
    for i, k in enumerate(ask_keys, start=1):
        ex_ask = rec[k]
        bids_a, asks_a = cache.get((base, ex_ask, "spot"), ([], []))
        row[f"ask_a_{i}"] = asks_a
        row[f"bid_a_{i}"] = bids_a

    return row


# ─── Helpers ─────────────────────────────────────────────────────────────────

def dump_decimal(obj) -> str:
    raw = json.dumps(obj, indent=2, ensure_ascii=False)
    def fix(m): return format(Decimal(m.group(0)), 'f')
    return re.sub(r'-?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', fix, raw)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    input_file  = Path("funding_rates_v3.json")
    output_file = Path("funding_rates_v4.json")

    with input_file.open(encoding="utf-8") as f:
        records: list[dict] = json.load(f)

    ff_recs = [r for r in records if r.get("strategy") == "ff"]
    sf_recs = [r for r in records if r.get("strategy") == "sf"]

    # Count total requests before deduplication
    total_naive = sum(
        2 if r.get("strategy") == "ff"
        else 1 + sum(1 for k in r if k.startswith("exchange_ask_"))
        for r in records
    )

    unique_keys = collect_keys(records)
    saved = total_naive - len(unique_keys)

    print(f"Loaded  : {len(records)} records  (ff={len(ff_recs)}, sf={len(sf_recs)})")
    print(f"Requests: {total_naive} naive  ->  {len(unique_keys)} unique  ({saved} duplicates skipped)")
    print("Fetching orderbooks...\n")
    print(f"{'#':>9}  {'elapsed':>7}  {'exchange':8}  {'market':7}  {'symbol':25}  {'req_t':>6}  status")
    print("-" * 80)

    global _done_count, _total_count, _t_start
    _done_count = 0
    _total_count = len(unique_keys)
    _t_start = time.monotonic()

    connector = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=connector) as session:
        results = await asyncio.gather(
            *[_fetch_one(session, key) for key in unique_keys]
        )

    total_elapsed = time.monotonic() - _t_start
    cache: dict[tuple, tuple[list, list]] = {key: ob for key, ob in results}
    empty_count = sum(1 for _, ob in results if len(ob[0]) == 0)

    # Build output preserving original spread-sorted order
    output = []
    for rec in records:
        if rec.get("strategy") == "ff":
            output.append(build_ff(rec, cache))
        else:
            output.append(build_sf(rec, cache))

    with output_file.open("w", encoding="utf-8") as f:
        f.write(dump_decimal(output))

    print("-" * 80)
    print(f"Done    : {_total_count} requests in {total_elapsed:.1f}s  ({total_elapsed/_total_count:.2f}s avg)")
    print(f"Empty   : {empty_count} orderbooks")
    print(f"Saved   : {len(output)} records -> {output_file}")


if __name__ == "__main__":
    asyncio.run(main())

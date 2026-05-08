"""
test_orderbooks.py
==================
Tests every exchange × market combination used by fetch_orderbooks.py.

For each of the 8 exchanges it sends:
  - 1 futures orderbook request  (same URL, same headers as production)
  - 1 spot    orderbook request  (same URL, same headers as production)

After all 16 requests complete, prints a detailed per-test report plus
a summary with pass / empty / error counts and average response times.

Usage:
    python test_orderbooks.py
"""

import asyncio
import aiohttp
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── import production helpers so we test EXACTLY the same logic ───────────────
sys.path.insert(0, str(Path(__file__).parent))
from fetch_orderbooks import (          # noqa: E402
    futures_url, spot_url, parse_ob,
    _EXCHANGE_HEADERS, LIMIT,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ─── Test matrix ──────────────────────────────────────────────────────────────
# futures_sym  — exact symbol as used by the production pipeline
# spot_base    — base coin; spot_url() will append USDT / -USDT / _USDT etc.

TEST_MATRIX: dict[str, dict] = {
    "Binance": {"futures_sym": "BTCUSDT",       "spot_base": "BTC"},
    "BingX":   {"futures_sym": "BTC-USDT",      "spot_base": "BTC"},
    "Bitget":  {"futures_sym": "BTCUSDT",        "spot_base": "BTC"},
    "Bybit":   {"futures_sym": "BTCUSDT",        "spot_base": "BTC"},
    "Gate.io": {"futures_sym": "BTC_USDT",       "spot_base": "BTC"},
    "KuCoin":  {"futures_sym": "XBTUSDTM",       "spot_base": "BTC"},
    "MEXC":    {"futures_sym": "BTC_USDT",       "spot_base": "BTC"},
    "OKX":     {"futures_sym": "BTC-USDT-SWAP",  "spot_base": "BTC"},
}


# ─── Result container ─────────────────────────────────────────────────────────

@dataclass
class Result:
    exchange:    str
    market:      str          # "futures" | "spot"
    symbol:      str
    url:         str  = ""
    http_status: int  = 0
    elapsed_ms:  float = 0.0
    bid_count:   int  = 0
    ask_count:   int  = 0
    top_bid:     str  = ""
    top_ask:     str  = ""
    error:       str  = ""

    @property
    def verdict(self) -> str:
        if self.error:           return "ERROR"
        if self.bid_count == 0:  return "EMPTY"
        return "OK"

    @property
    def ok(self) -> bool:
        return self.verdict == "OK"


# ─── Single test ──────────────────────────────────────────────────────────────

async def run_test(
    session:  aiohttp.ClientSession,
    exchange: str,
    market:   str,
    symbol:   str,
) -> Result:
    r = Result(exchange=exchange, market=market, symbol=symbol)

    r.url = futures_url(symbol, exchange) if market == "futures" else spot_url(symbol, exchange)
    if not r.url:
        r.error = "no URL builder for this exchange/market combination"
        return r

    headers = _EXCHANGE_HEADERS.get(exchange)
    t0 = time.monotonic()

    try:
        async with session.get(
            r.url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            r.http_status = resp.status
            data = await resp.json(content_type=None)

        r.elapsed_ms = (time.monotonic() - t0) * 1000

        if r.http_status != 200:
            r.error = f"HTTP {r.http_status}"
            return r

        bids, asks = parse_ob(data, exchange, market)
        r.bid_count = len(bids)
        r.ask_count = len(asks)

        if bids:
            r.top_bid = str(bids[0][0])
        if asks:
            r.top_ask = str(asks[0][0])

    except Exception as exc:
        r.elapsed_ms = (time.monotonic() - t0) * 1000
        r.error = f"{type(exc).__name__}: {exc}"

    return r


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _bar(ok: int, total: int, width: int = 20) -> str:
    filled = round(ok / total * width) if total else 0
    return "[" + "#" * filled + "." * (width - filled) + "]"


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    total_tests = len(TEST_MATRIX) * 2  # futures + spot per exchange

    print("=" * 80)
    print(f"  Orderbook API Test  —  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  {len(TEST_MATRIX)} exchanges  x  2 markets (futures + spot)  =  {total_tests} tests")
    print(f"  Symbol used: BTC  |  Orderbook depth: {LIMIT}")
    print("=" * 80)
    print()

    # ── build task list in display order ──────────────────────────────────────
    tasks: list[tuple[str, str, str]] = []
    for exchange, cfg in TEST_MATRIX.items():
        tasks.append((exchange, "futures", cfg["futures_sym"]))
        tasks.append((exchange, "spot",    cfg["spot_base"]))

    t_start = time.monotonic()

    connector = aiohttp.TCPConnector(limit=50)
    async with aiohttp.ClientSession(connector=connector) as session:
        results: list[Result] = await asyncio.gather(
            *[run_test(session, ex, mkt, sym) for ex, mkt, sym in tasks]
        )

    total_elapsed = time.monotonic() - t_start

    # ── per-test table ────────────────────────────────────────────────────────
    COL = "{:<10}  {:<7}  {:<20}  {:<5}  {:>4}  {:>7}  {:>4}  {:>4}  {:>15}  {:>15}"
    HDR = COL.format(
        "Exchange", "Market", "Symbol", "Result",
        "HTTP", "ms", "Bids", "Asks", "Top bid", "Top ask",
    )
    SEP = "-" * len(HDR)

    print(HDR)
    print(SEP)

    prev_exchange = ""
    for r in results:
        if prev_exchange and r.exchange != prev_exchange:
            print()  # blank line between exchange groups
        prev_exchange = r.exchange

        err_note = f"  <- {r.error}" if r.error else ""
        print(COL.format(
            r.exchange, r.market, r.symbol, r.verdict,
            r.http_status if r.http_status else "-",
            f"{r.elapsed_ms:.0f}",
            r.bid_count, r.ask_count,
            r.top_bid or "-", r.top_ask or "-",
        ) + err_note)

    print(SEP)

    # ── URL reference ─────────────────────────────────────────────────────────
    print()
    print("URLs tested (same as production):")
    print("-" * 80)
    for r in results:
        mark = "OK " if r.ok else ("   " if not r.error else "ERR")
        print(f"  [{mark}]  {r.exchange:<10}  {r.market:<7}  {r.url}")

    # ── summary ───────────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.ok)
    empty  = sum(1 for r in results if r.verdict == "EMPTY")
    errors = sum(1 for r in results if r.verdict == "ERROR")

    ok_200    = [r for r in results if r.http_status == 200]
    avg_ms    = sum(r.elapsed_ms for r in ok_200) / len(ok_200) if ok_200 else 0
    max_ms    = max((r.elapsed_ms for r in ok_200), default=0)
    slowest   = max(ok_200, key=lambda r: r.elapsed_ms, default=None)

    print()
    print("=" * 80)
    print(f"  RESULT  {_bar(passed, total_tests)}  {passed}/{total_tests} passed")
    print()
    print(f"  OK     : {passed:>2}")
    print(f"  EMPTY  : {empty:>2}  (HTTP 200 but bids/asks are empty — likely rate-limited)")
    print(f"  ERROR  : {errors:>2}  (network error or non-200 HTTP status)")
    print()
    print(f"  Total time   : {total_elapsed:.2f} s  (all {total_tests} requests in parallel)")
    print(f"  Avg resp time: {avg_ms:.0f} ms  (HTTP 200 responses only)")
    print(f"  Slowest      : {f'{slowest.exchange} / {slowest.market}  {max_ms:.0f} ms' if slowest else '-'}")

    if empty or errors:
        print()
        print("  Issues found:")
        for r in results:
            if not r.ok:
                detail = r.error if r.error else "empty bids/asks"
                print(f"    [{r.verdict}]  {r.exchange:<10}  {r.market:<7}  {r.symbol}  —  {detail}")

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

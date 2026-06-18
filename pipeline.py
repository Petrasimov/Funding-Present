"""
pipeline.py — In-memory funding rate arbitrage pipeline.

run_pipeline() -> list[dict]

Steps:
  1. Fetch funding rates from 8 exchanges
  2. Filter to active contracts only
  3. Build FF pairs (Futures/Futures)
  4. Build SF list (Spot/Futures) with spot exchange enrichment
  5. Merge + sort by spread descending
  6. Return result — no files written
"""

import asyncio
import aiohttp

from core.fetchers  import fetch_all
from core.filters   import filter_active
from core.strategies import build_ff_pairs, build_sf_list


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


async def run_pipeline() -> list[dict]:
    """
    Run the full pipeline in-memory.
    Returns merged FF+SF list sorted by spread descending.
    """
    timeout   = aiohttp.ClientTimeout(total=90)
    connector = aiohttp.TCPConnector(limit=100)

    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers=_HEADERS,
    ) as session:
        # Step 1: fetch funding rates
        raw_records = await fetch_all(session)

        # Step 2: filter inactive contracts
        active_records = await filter_active(raw_records, session)

        # Step 3: FF pairs (sync, no HTTP needed)
        ff_pairs = build_ff_pairs(active_records)

        # Step 4: SF list (needs HTTP for spot exchange lists)
        sf_list = await build_sf_list(active_records, session)

    # Step 5: merge + sort
    merged = ff_pairs + sf_list
    merged.sort(key=lambda x: x["spread"], reverse=True)

    print(f"[pipeline] Done — {len(ff_pairs)} FF + {len(sf_list)} SF = {len(merged)} total")
    return merged
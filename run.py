"""
run.py — Master loop for funding rate arbitrage pipeline.

Each cycle:
  1. run_pipeline() — fetch, filter, build FF+SF in-memory
  2. save_results()  — write to PostgreSQL
  3. Print summary
  4. Wait CYCLE_INTERVAL seconds
  5. Repeat

Press Ctrl+C to stop after the current cycle completes.
"""

import asyncio
import time
from datetime import datetime

from pipeline import run_pipeline
from db import create_pool, save_results

CYCLE_INTERVAL = 60  # seconds between cycles


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def separator(char: str = "─", width: int = 70) -> None:
    print(char * width, flush=True)


async def main() -> None:
    separator("═")
    print("  Funding Rate Arbitrage Pipeline")
    print(f"  Cycle interval: {CYCLE_INTERVAL}s")
    print("  Press Ctrl+C to stop after the current cycle completes.")
    separator("═")

    pool = await create_pool()
    print(f"[{now()}]  DB connected\n")

    cycle = 0
    last_results: list[dict] = []

    try:
        while True:
            cycle += 1
            t0 = time.monotonic()

            separator()
            print(f"[{now()}]  CYCLE {cycle} started")
            separator()

            try:
                results = await run_pipeline()
                elapsed = time.monotonic() - t0

                ff_count = sum(1 for r in results if r.get("strategy") == "ff")
                sf_count = sum(1 for r in results if r.get("strategy") == "sf")

                if results:
                    last_results = results
                    await save_results(pool, results, cycle, ff_count, sf_count, elapsed)

                separator()
                print(f"[{now()}]  CYCLE {cycle} complete in {elapsed:.1f}s")
                print(f"           FF: {ff_count}  |  SF: {sf_count}  |  Total: {len(results)}")
                if results:
                    top = results[0]
                    ex  = f"{top.get('exchange_bid','?')} / {top.get('exchange_ask', top.get('exchange_ask_1','?'))}"
                    print(f"           Top: {top['symbol']}  {top['spread']:.4f}%  [{ex}]")
                separator()

            except Exception as e:
                elapsed = time.monotonic() - t0
                separator()
                print(f"[{now()}]  CYCLE {cycle} FAILED in {elapsed:.1f}s — {e}")
                if last_results:
                    print(f"           Using last successful data ({len(last_results)} records)")
                separator()

            print(f"[{now()}]  Next cycle in {CYCLE_INTERVAL}s...\n", flush=True)
            await asyncio.sleep(CYCLE_INTERVAL)

    except KeyboardInterrupt:
        separator("═")
        print(f"[{now()}]  Ctrl+C — stopping after cycle {cycle}.")
        separator("═")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
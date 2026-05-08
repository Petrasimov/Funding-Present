"""
run.py — Master loop for funding-rate arbitrage pipeline.

Runs the full pipeline continuously until Ctrl+C:
  1. fetch_funding_rates.py     → funding_rates.json
  2. filter_active_contracts.py → funding_rates_v2.json
  3. split_strategies.py        → ff_rates.json + sf_rates_v_1.json
  4. enrich_sf_rates.py         → sf_rates_v_2.json
  5. merge_rates.py             → funding_rates_v3.json
  6. fetch_orderbooks.py        → funding_rates_v4.json  ← final output

On step failure: logs the error, uses last successful file, continues.
On Ctrl+C:       finishes the current cycle then exits cleanly.
"""

import subprocess
import sys
import time
import os
from datetime import datetime
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

WORKDIR = Path(__file__).parent

STEPS = [
    ("Funding rates",        "fetch_funding_rates.py"),
    ("Active contracts",     "filter_active_contracts.py"),
    ("Split strategies",     "split_strategies.py"),
    ("Enrich SF rates",      "enrich_sf_rates.py"),
    ("Merge rates",          "merge_rates.py"),
    ("Orderbooks",           "fetch_orderbooks.py"),
]

# ─── Helpers ─────────────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}]  {msg}", flush=True)


def separator(char: str = "-", width: int = 70) -> None:
    print(char * width, flush=True)


def run_step(name: str, script: str) -> tuple[bool, float]:
    """
    Run a pipeline step.
    Returns (success, elapsed_seconds).
    On failure: prints stderr, returns (False, elapsed).
    """
    t0 = time.monotonic()
    log(f"START  {name}  ({script})")

    result = subprocess.run(
        [sys.executable, str(WORKDIR / script)],
        cwd=str(WORKDIR),
        capture_output=False,   # stream stdout/stderr live to terminal
    )

    elapsed = time.monotonic() - t0
    ok = result.returncode == 0

    status = "OK" if ok else f"FAILED (exit {result.returncode})"
    log(f"END    {name}  {status}  [{elapsed:.1f}s]")
    return ok, elapsed


# ─── Main loop ───────────────────────────────────────────────────────────────

def main() -> None:
    separator("=")
    print("  Funding Rate Arbitrage Pipeline  —  run.py")
    print("  Press Ctrl+C to stop after the current cycle completes.")
    separator("=")

    cycle = 0
    stop_requested = False

    try:
        while True:
            cycle += 1
            cycle_start = time.monotonic()

            separator()
            log(f"CYCLE {cycle} started")
            separator()

            step_results: list[tuple[str, bool, float]] = []

            for name, script in STEPS:
                ok, elapsed = run_step(name, script)
                step_results.append((name, ok, elapsed))

                if not ok:
                    log(f"WARNING: '{name}' failed — continuing with last saved data")

                print(flush=True)

            # ── Cycle summary ─────────────────────────────────────────────
            cycle_elapsed = time.monotonic() - cycle_start
            failed = [n for n, ok, _ in step_results if not ok]

            separator()
            log(f"CYCLE {cycle} complete in {cycle_elapsed:.1f}s")
            if failed:
                log(f"  Failed steps: {', '.join(failed)}")
            else:
                log("  All steps succeeded")
            log("  Output: funding_rates_v4.json")
            separator()

            if stop_requested:
                break

            print(flush=True)

    except KeyboardInterrupt:
        separator("=")
        log("Ctrl+C received — stopping after this cycle.")
        separator("=")

    log(f"Pipeline stopped after {cycle} cycle(s).")


if __name__ == "__main__":
    main()

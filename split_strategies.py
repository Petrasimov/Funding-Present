"""
Splits funding_rates_v2.json into two strategy files:

ff_rates.json     — Futures/Futures arbitrage
  SHORT on exchange_bid (higher funding -> shorts earn / longs pay more)
  LONG  on exchange_ask (lower  funding -> longs earn  / shorts pay less)
  spread = funding_rate_bid − funding_rate_ask  (always positive)

sf_rates_v_1.json — Spot/Futures arbitrage (cash-and-carry)
  Only symbols with positive funding rate.
  We short futures (collect funding) and hold spot as hedge.
  Fields: symbol, exchange, funding_rate
"""

import json
import re
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from collections import defaultdict


# --- Helpers -----------------------------------------------------------------

def dump_decimal(obj) -> str:
    """json.dumps with floats in decimal notation, never scientific."""
    raw = json.dumps(obj, indent=2, ensure_ascii=False)

    def fix(m: re.Match) -> str:
        return format(Decimal(m.group(0)), 'f')

    return re.sub(r'-?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', fix, raw)


def fmt_pct(v: float) -> str:
    """Format a decimal rate as readable percentage string, e.g. 0.00043 -> '+0.043%'"""
    pct = v * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.4f}%"


# --- Load ---------------------------------------------------------------------

input_file = Path("funding_rates_v2.json")
if not input_file.exists():
    raise FileNotFoundError(f"{input_file} not found. Run filter_active_contracts.py first.")

with input_file.open(encoding="utf-8") as f:
    records: list[dict] = json.load(f)

print(f"Loaded {len(records)} records from {input_file}\n")


# --- ff_rates.json ------------------------------------------------------------
#
# Logic:
#   Positive funding -> longs PAY shorts  -> short earns, long pays
#   Negative funding -> shorts PAY longs  -> long earns, short pays
#
#   For a pair (A, B):
#     SHORT on A, LONG on B  ->  net = rate_A − rate_B
#     We pick the ordering that gives net > 0, i.e. rate_bid > rate_ask
#
#   exchange_bid  = where we SHORT (higher rate)
#   exchange_ask  = where we LONG  (lower  rate)
#   spread        = funding_rate_bid − funding_rate_ask  (> 0)

# Group all entries by symbol
by_symbol: dict[str, list[dict]] = defaultdict(list)
for rec in records:
    by_symbol[rec["symbol"]].append(rec)

ff_rows: list[dict] = []

for symbol, entries in by_symbol.items():
    if len(entries) < 2:
        continue  # needs at least two exchanges

    for a, b in combinations(entries, 2):
        # Determine which side is bid (short / higher rate) and ask (long / lower rate)
        if a["funding_rate"] >= b["funding_rate"]:
            bid, ask = a, b
        else:
            bid, ask = b, a

        spread = bid["funding_rate"] - ask["funding_rate"]

        # Only include pairs where spread is strictly positive
        if spread <= 0:
            continue

        ff_rows.append({
            "symbol":            symbol,
            "exchange_bid":      bid["exchange"],
            "exchange_ask":      ask["exchange"],
            "funding_rate_bid":  bid["funding_rate"],
            "funding_rate_ask":  ask["funding_rate"],
            "spread":            round(spread * 100, 10),
        })

# Sort best opportunities first
ff_rows.sort(key=lambda x: x["spread"], reverse=True)

ff_file = Path("ff_rates.json")
with ff_file.open("w", encoding="utf-8") as f:
    f.write(dump_decimal(ff_rows))

print(f"ff_rates.json  -> {len(ff_rows):5d} pairs  (Futures/Futures arbitrage)")


# --- sf_rates_v_1.json --------------------------------------------------------
#
# Cash-and-carry: short futures + buy spot.
# We earn funding only when rate is POSITIVE (shorts receive payment).

sf_rows: list[dict] = [
    {
        "symbol":       rec["symbol"],
        "exchange":     rec["exchange"],
        "funding_rate": rec["funding_rate"],
    }
    for rec in records
    if rec["funding_rate"] > 0
]

# Sort highest funding first
sf_rows.sort(key=lambda x: x["funding_rate"], reverse=True)

sf_file = Path("sf_rates_v_1.json")
with sf_file.open("w", encoding="utf-8") as f:
    f.write(dump_decimal(sf_rows))

print(f"sf_rates_v_1.json -> {len(sf_rows):5d} records (Spot/Futures arbitrage)")

# --- Preview top-5 of each ---------------------------------------------------

print("\n-- Top-5 FF opportunities --")
for row in ff_rows[:5]:
    print(
        f"  {row['symbol']:15s} "
        f"{row['exchange_bid']:8s} SHORT {fmt_pct(row['funding_rate_bid']):>10s}  |  "
        f"{row['exchange_ask']:8s} LONG  {fmt_pct(row['funding_rate_ask']):>10s}  |  "
        f"spread {fmt_pct(row['spread'])}"
    )

print("\n-- Top-5 SF opportunities --")
for row in sf_rows[:5]:
    print(f"  {row['symbol']:15s}  {row['exchange']:8s}  {fmt_pct(row['funding_rate']):>10s}")

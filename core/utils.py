"""
core/utils.py — Shared helpers used across the pipeline.
"""

import json
import re
from decimal import Decimal


_EXCLUDED = {0.0, 0.00005, -0.00005}


def valid(v: float) -> bool:
    """Return True if funding rate is a real value, not a zero or exchange default."""
    return v not in _EXCLUDED


def fmt(v: float) -> float:
    """Round-trip through Decimal for clean float representation."""
    return float(Decimal(repr(v)))


def fmt_pct(v: float) -> str:
    """Format a decimal rate as percentage string: 0.00043 -> '+0.043%'"""
    pct = v * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.4f}%"


def dump_decimal(obj) -> str:
    """json.dumps with floats in decimal notation, never scientific."""
    raw = json.dumps(obj, indent=2, ensure_ascii=False)

    def fix(m: re.Match) -> str:
        return format(Decimal(m.group(0)), 'f')

    return re.sub(r'-?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', fix, raw)


def base_from_futures(symbol: str, exchange: str) -> str:
    """Extract base coin (e.g. 'BTC') from a futures symbol."""
    if exchange == "KuCoin":
        if symbol.endswith("USDTM"):
            return symbol[:-5]
        if symbol.endswith("USDM"):
            return symbol[:-4]
    elif exchange == "OKX":
        return symbol.split("-")[0]
    elif exchange in ("Gate.io", "MEXC"):
        return symbol.split("_")[0]
    elif exchange == "BingX":
        return symbol.split("-")[0]
    elif exchange in ("Binance", "Bybit", "Bitget"):
        if symbol.endswith("USDT"):
            return symbol[:-4]
    return symbol
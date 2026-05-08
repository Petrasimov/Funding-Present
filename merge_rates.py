import json, re
from decimal import Decimal
from pathlib import Path

def dump_decimal(obj) -> str:
    raw = json.dumps(obj, indent=2, ensure_ascii=False)
    def fix(m): return format(Decimal(m.group(0)), 'f')
    return re.sub(r'-?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+', fix, raw)

def load(path: str, strategy: str) -> list[dict]:
    with Path(path).open(encoding="utf-8") as f:
        records = json.load(f)
    result = []
    for rec in records:
        row = {"symbol": rec.pop("symbol"), "strategy": strategy}
        row.update(rec)
        result.append(row)
    return result

sf = load("sf_rates_v_2.json", "sf")
ff = load("ff_rates.json", "ff")

merged = sorted(sf + ff, key=lambda x: x.get("spread", 0), reverse=True)

Path("funding_rates_v3.json").write_text(dump_decimal(merged), encoding="utf-8")
print(f"sf: {len(sf)}, ff: {len(ff)}, total: {len(merged)} -> funding_rates_v3.json")

"""
api_server.py — FastAPI server for funding rate arbitrage data.

Endpoints:
  GET /api/funding/rates          — all opportunities, sorted by spread
  GET /api/funding/rates?strategy=ff
  GET /api/funding/rates?strategy=sf
  GET /api/funding/meta           — last 20 cycle records
  GET /api/funding/health         — server status

Run:
  python api_server.py
  uvicorn api_server:app --host 0.0.0.0 --port 5001
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from db import create_pool, load_results, load_meta


# ─── App & lifespan ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    print(f"[api] DB pool created")
    yield
    await app.state.pool.close()
    print(f"[api] DB pool closed")


app = FastAPI(
    title="Funding Rate Arbitrage API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _serialize(row: dict) -> dict:
    """Convert asyncpg row to JSON-serializable dict."""
    result = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/api/funding/health")
async def health():
    return {
        "status": "ok",
        "time":   datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/funding/rates")
async def get_rates(
    strategy: str | None = Query(default=None, pattern="^(ff|sf)$"),
    limit:    int        = Query(default=500, ge=1, le=2000),
):
    """
    Returns funding arbitrage opportunities sorted by spread descending.
    Optional filter: strategy=ff or strategy=sf
    """
    try:
        rows = await load_results(app.state.pool, strategy=strategy, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "count":    len(rows),
        "strategy": strategy or "all",
        "data":     [_serialize(r) for r in rows],
    }


@app.get("/api/funding/meta")
async def get_meta():
    """Returns last 20 pipeline cycle records."""
    try:
        rows = await load_meta(app.state.pool, last_n=20)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "count": len(rows),
        "data":  [_serialize(r) for r in rows],
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001, log_level="info")
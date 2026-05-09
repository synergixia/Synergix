"""
dexscreener.py — Real market data for SYNERGIX from DexScreener public API.

No API key required. Returns price, 24h change, volume, liquidity and FDV
for the highest-liquidity pair.
"""

import logging
import time
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"
_CACHE_TTL = 60  # seconds
_cache: Dict[str, Dict[str, Any]] = {}


async def get_token_market(token_address: str) -> Optional[Dict[str, Any]]:
    """
    Fetch live market data for a BSC token from DexScreener.
    Picks the pair with the highest USD liquidity.
    Returns None if the API is unreachable or no pair exists.
    """
    cache_key = token_address.lower()
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached["ts"] < _CACHE_TTL:
        return cached["data"]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{DEXSCREENER_TOKEN_URL}/{token_address}")
            r.raise_for_status()
            payload = r.json()
    except Exception as exc:
        logger.warning("DexScreener fetch failed: %s", exc)
        return None

    pairs = payload.get("pairs") or []
    if not pairs:
        return None

    # Filter to BSC pairs only and pick the most liquid one
    bsc_pairs = [p for p in pairs if (p.get("chainId") or "").lower() == "bsc"]
    candidates = bsc_pairs or pairs
    best = max(
        candidates,
        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
    )

    txns_h24 = (best.get("txns") or {}).get("h24") or {}

    result = {
        "dex_id": best.get("dexId", ""),
        "pair_address": best.get("pairAddress", ""),
        "pair_url": best.get("url", ""),
        "price_usd": float(best.get("priceUsd") or 0),
        "price_native": float(best.get("priceNative") or 0),
        "price_change_24h": float((best.get("priceChange") or {}).get("h24") or 0),
        "volume_24h_usd": float((best.get("volume") or {}).get("h24") or 0),
        "liquidity_usd": float((best.get("liquidity") or {}).get("usd") or 0),
        "fdv": float(best.get("fdv") or 0),
        "market_cap": float(best.get("marketCap") or 0),
        "txns_24h_buys": int(txns_h24.get("buys") or 0),
        "txns_24h_sells": int(txns_h24.get("sells") or 0),
    }

    _cache[cache_key] = {"data": result, "ts": now}
    return result


__all__ = ["get_token_market"]

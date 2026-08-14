"""DexScreener — harga, likuiditas per-pool, txns, volume, sosial."""
from __future__ import annotations

from typing import Any

from ..http import get_json

BASE = "https://api.dexscreener.com"


async def fetch_pairs(mint: str) -> list[dict[str, Any]]:
    data = await get_json(
        f"{BASE}/latest/dex/tokens/{mint}",
        cache_key=f"ds:{mint}",
    )
    if not isinstance(data, dict):
        return []
    pairs = data.get("pairs") or []
    return [p for p in pairs if isinstance(p, dict) and p.get("chainId") == "solana"]


def _liq(pair: dict) -> float:
    return float((pair.get("liquidity") or {}).get("usd") or 0.0)


def best_pair(pairs: list[dict]) -> dict:
    """Pool dengan likuiditas terbesar — ini yang dipakai sebagai referensi harga."""
    if not pairs:
        return {}
    return max(pairs, key=_liq)


def total_liquidity(pairs: list[dict]) -> float:
    return sum(_liq(p) for p in pairs)


def socials(pair: dict) -> dict[str, str]:
    info = pair.get("info") or {}
    out: dict[str, str] = {}
    for w in info.get("websites") or []:
        if isinstance(w, dict) and w.get("url"):
            out.setdefault("website", w["url"])
    for s in info.get("socials") or []:
        if isinstance(s, dict) and s.get("url"):
            out.setdefault((s.get("type") or "link").lower(), s["url"])
    return out

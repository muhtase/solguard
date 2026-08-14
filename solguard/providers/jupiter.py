"""Jupiter Token API v2 — organic score (volume yang sudah difilter dari bot/wash),
holder count, audit ringkas, dan statistik trader per interval.

Ini sumber paling berharga untuk menilai apakah aktivitas token itu ASLI
atau cuma volume palsu hasil wash trading.
"""
from __future__ import annotations

from typing import Any

from ..http import get_json

BASE = "https://lite-api.jup.ag/tokens/v2"


async def fetch_token(mint: str) -> dict[str, Any] | None:
    data = await get_json(
        f"{BASE}/search",
        params={"query": mint},
        cache_key=f"jup:{mint}",
    )
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id") == mint:
                return item
        return data[0] if data and isinstance(data[0], dict) else None
    if isinstance(data, dict):
        # Beberapa response membungkus hasil di dalam "tokens"
        tokens = data.get("tokens")
        if isinstance(tokens, list) and tokens:
            return tokens[0]
        return data if data.get("id") else None
    return None


def stats(token: dict, window: str = "24h") -> dict:
    return token.get(f"stats{window}") or {}


def organic_volume_ratio(token: dict, window: str = "24h") -> float | None:
    """Porsi volume yang dianggap organik oleh Jupiter (0..1).

    Rasio rendah = volume didominasi bot / wash trading.
    """
    s = stats(token, window)
    buy = float(s.get("buyVolume") or 0)
    sell = float(s.get("sellVolume") or 0)
    total = buy + sell
    if total <= 0:
        return None
    organic = float(s.get("buyOrganicVolume") or 0) + float(s.get("sellOrganicVolume") or 0)
    return max(0.0, min(1.0, organic / total))

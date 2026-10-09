"""GMGN OpenAPI — top trader (dengan PnL & tag smart money) dan kline harian.

Catatan perilaku yang DIUKUR (lihat memory proyek trench, 6 Okt 2026):
  * host `openapi.gmgn.ai` bisa diakses dari VPS; `gmgn.ai` diblokir Cloudflare
  * auth read-only cukup header X-APIKEY + query timestamp (DETIK) & client_id
  * rate limit per IP: >1 req/detik -> RATE_LIMIT_BANNED sementara
  * kline: from/to WAJIB milidetik, maks 100 lilin per panggilan
  * nesting respons tidak konsisten antar endpoint -> _kupas()
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx

import config

from ..http import cache_get, cache_set, get_client

log = logging.getLogger(__name__)

_pace_lock = asyncio.Lock()
_last_call = 0.0
_banned_until = 0.0


def enabled() -> bool:
    return bool(config.GMGN_API_KEY)


def _kupas(d: Any) -> Any:
    if isinstance(d, dict) and "code" in d and isinstance(d.get("data"), dict):
        return d["data"]
    return d


def daftar(d: Any) -> list:
    if isinstance(d, list):
        return d
    if not isinstance(d, dict):
        return []
    for k in ("list", "rank", "activities", "items", "data"):
        if isinstance(d.get(k), list):
            return d[k]
    return []


async def _call(path: str, query: dict[str, str], *, cache_key: str | None = None,
                cache_ttl: int = 300) -> Any | None:
    """Satu panggilan GMGN dengan pacing global + backoff 429. None kalau gagal."""
    if not enabled():
        return None
    if cache_key:
        hit = cache_get(cache_key)
        if hit is not None:
            return hit

    global _last_call, _banned_until
    client = await get_client()
    for attempt in range(3):
        async with _pace_lock:
            now = time.monotonic()
            wait = max(_last_call + config.GMGN_MIN_INTERVAL - now, _banned_until - now)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_call = time.monotonic()
            try:
                resp = await client.get(
                    f"{config.GMGN_HOST}{path}",
                    params={
                        **query,
                        "timestamp": str(int(time.time())),
                        "client_id": str(uuid.uuid4()),
                    },
                    headers={"X-APIKEY": config.GMGN_API_KEY, "User-Agent": "gmgn-cli"},
                )
            except httpx.HTTPError as exc:
                log.warning("GMGN %s gagal: %s", path, exc)
                return None

        if resp.status_code == 429:
            # Dibanned sementara; mundur agresif supaya tidak memperpanjang ban
            # dan tidak mengganggu trench-kolektor yang berbagi IP ini.
            _banned_until = time.monotonic() + 5.0 * (attempt + 1)
            log.warning("GMGN 429 di %s, mundur %.0fs", path, 5.0 * (attempt + 1))
            continue
        if resp.status_code != 200:
            log.warning("GMGN %s -> HTTP %s", path, resp.status_code)
            return None
        try:
            js = resp.json()
        except ValueError:
            return None
        if js.get("code") != 0:
            log.warning("GMGN %s code=%s %s", path, js.get("code"), js.get("message", ""))
            return None
        data = _kupas(js.get("data"))
        if cache_key:
            cache_set(cache_key, data, cache_ttl)
        return data
    return None


async def top_traders(mint: str) -> list[dict]:
    """Top 100 trader token: PnL realisasi, tag wallet (smart_degen dll), nama CEX."""
    d = await _call(
        "/v1/market/token_top_traders",
        {"chain": "sol", "address": mint},
        cache_key=f"gmgn:tt:{mint}",
    )
    return [x for x in daftar(d) if isinstance(x, dict)]


async def kline_daily(mint: str, max_pages: int | None = None) -> list[dict]:
    """Lilin harian, dipaginasi mundur maks `max_pages` x 100 hari.

    Return list dict {time(ms), open, high, low, close, volume(usd)} terurut naik.
    Angka harga di respons berupa string -> dikonversi float di sini.
    """
    pages = max_pages if max_pages is not None else config.GMGN_KLINE_PAGES_MAX
    ck = f"gmgn:kl:{mint}:{pages}"
    hit = cache_get(ck)
    if hit is not None:
        return hit

    day_ms = 86_400_000
    semua: dict[int, dict] = {}
    sampai = int(time.time() * 1000)
    for _ in range(pages):
        dari = sampai - 100 * day_ms
        d = await _call(
            "/v1/market/token_kline",
            {"chain": "sol", "address": mint, "resolution": "1d",
             "from": str(dari), "to": str(sampai)},
        )
        lst = daftar(d)
        if not lst:
            break
        for c in lst:
            try:
                t = int(c["time"])
                semua[t] = {
                    "time": t,
                    "open": float(c.get("open") or 0),
                    "high": float(c.get("high") or 0),
                    "low": float(c.get("low") or 0),
                    "close": float(c.get("close") or 0),
                    "volume": float(c.get("volume") or 0),
                }
            except (KeyError, TypeError, ValueError):
                continue
        if len(lst) < 100:
            break
        sampai = min(int(c["time"]) for c in lst if "time" in c) - 1

    out = sorted(semua.values(), key=lambda c: c["time"])
    cache_set(ck, out, 600)
    return out

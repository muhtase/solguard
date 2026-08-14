"""Shared async HTTP client + cache TTL sederhana in-memory."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

import config

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

_cache: dict[str, tuple[float, Any]] = {}


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=config.HTTP_TIMEOUT,
                    follow_redirects=True,
                    headers={
                        "User-Agent": "solguard-bot/1.0",
                        "Accept": "application/json",
                    },
                    limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
                )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if not hit:
        return None
    expires, value = hit
    if time.time() > expires:
        _cache.pop(key, None)
        return None
    return value


def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    _cache[key] = (time.time() + (ttl if ttl is not None else config.CACHE_TTL), value)


def cache_purge() -> None:
    now = time.time()
    for k in [k for k, (exp, _) in _cache.items() if now > exp]:
        _cache.pop(k, None)


def cache_drop(substring: str) -> int:
    """Buang entri cache yang mengandung `substring`.

    Dipakai tombol Refresh: tanpa ini, klik refresh cuma menyajikan ulang data
    lama dari cache dan tombolnya jadi tidak ada gunanya.
    """
    keys = [k for k in _cache if substring in k]
    for k in keys:
        _cache.pop(k, None)
    return len(keys)


async def get_json(
    url: str,
    *,
    params: dict | None = None,
    retries: int = 2,
    cache_key: str | None = None,
    cache_ttl: int | None = None,
) -> Any | None:
    """GET JSON. Return None kalau gagal — caller wajib handle degradasi."""
    if cache_key:
        cached = cache_get(cache_key)
        if cached is not None:
            return cached

    client = await get_client()
    delay = 0.6
    for attempt in range(retries + 1):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if resp.status_code >= 500:
                if attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return None
            if resp.status_code != 200:
                log.warning("GET %s -> HTTP %s", url, resp.status_code)
                return None
            data = resp.json()
            if cache_key:
                cache_set(cache_key, data, cache_ttl)
            return data
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("GET %s gagal (%s): %s", url, attempt, exc)
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
    return None


async def post_json(url: str, payload: Any, *, retries: int = 3) -> Any | None:
    """POST JSON. `payload` boleh dict (single) atau list (batch JSON-RPC)."""
    client = await get_client()
    delay = 0.7
    for attempt in range(retries + 1):
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code in (429, 503) or resp.status_code >= 500:
                if attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return None
            if resp.status_code != 200:
                return None
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("POST %s gagal (%s): %s", url, attempt, exc)
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
    return None

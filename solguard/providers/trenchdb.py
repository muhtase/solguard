"""Pembaca READ-ONLY database Tahap 0 proyek trench.

Tabel `kejadian_sm` berisi setiap kejadian beli/jual wallet yang GMGN tandai
smart money (tag smart_degen, launchpad_smart, dst), dipoll tiap 20 detik sejak
6 Okt 2026 — ~357 ribu kejadian dari ~1.300 wallet pada hari ke-3. Ini sumber
lokal untuk fitur D4 mage ("smart money absence") dan prinsip "wallet overlap":
nol panggilan API, nol beban rate limit.

Aturan keras: koneksi dibuka `mode=ro`. File ini milik eksperimen yang
pre-registrasinya dikunci; SolGuard hanya membaca, tidak pernah menulis.
Kalau file tidak ada (mis. trench dimatikan), semua fungsi mengembalikan None
dan laporan bilang terang-terangan bahwa sumbernya tidak tersedia.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field

import config

log = logging.getLogger(__name__)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_known_cache: tuple[float, set[str]] = (0.0, set())
_base_cache: tuple[float, dict] = (0.0, {})


@dataclass
class SmartMoneySummary:
    window_days: int
    events: int = 0
    makers: int = 0
    buy_usd: float = 0.0
    sell_usd: float = 0.0
    first_ts: int | None = None
    last_ts: int | None = None
    # 24 jam terakhir, untuk membaca arah terkini
    events_24h: int = 0
    makers_24h: int = 0
    buy_usd_24h: float = 0.0
    sell_usd_24h: float = 0.0
    # konteks: di mana token ini berdiri dibanding token lain yang disentuh SM
    makers_percentile: float | None = None
    tokens_touched_in_window: int = 0
    # kesehatan feed: kalau feed mati, "absen" tidak bermakna
    feed_last_event_ts: int | None = None
    feed_healthy: bool = False
    top_makers: list[tuple[str, float, str]] = field(default_factory=list)  # (maker, net_usd, tag)

    @property
    def net_usd(self) -> float:
        return self.buy_usd - self.sell_usd

    @property
    def net_usd_24h(self) -> float:
        return self.buy_usd_24h - self.sell_usd_24h


def available() -> bool:
    return os.path.exists(config.TRENCH_DB_PATH)


def _connect() -> sqlite3.Connection | None:
    global _conn
    if _conn is not None:
        return _conn
    if not available():
        return None
    try:
        uri = f"file:{config.TRENCH_DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=5)
        conn.execute("PRAGMA query_only = 1")
        _conn = conn
    except sqlite3.Error:
        log.warning("Gagal membuka DB trench (read-only)", exc_info=True)
        return None
    return _conn


def known_smart_wallets() -> set[str]:
    """Semua alamat wallet yang pernah muncul di feed smart money. Cache 10 menit."""
    global _known_cache
    ts, cached = _known_cache
    if cached and time.time() - ts < 600:
        return cached
    conn = _connect()
    if conn is None:
        return set()
    try:
        with _lock:
            rows = conn.execute("SELECT DISTINCT maker FROM kejadian_sm").fetchall()
        _known_cache = (time.time(), {r[0] for r in rows if r[0]})
    except sqlite3.Error:
        log.warning("Query wallet smart money gagal", exc_info=True)
        return cached
    return _known_cache[1]


def _baseline(conn: sqlite3.Connection, since: int) -> dict:
    """Distribusi jumlah maker unik per token dalam jendela — buat persentil.
    Mahal (scan jendela), jadi di-cache 10 menit."""
    global _base_cache
    ts, cached = _base_cache
    if cached and time.time() - ts < 600:
        return cached
    rows = conn.execute(
        "SELECT COUNT(DISTINCT maker) FROM kejadian_sm WHERE ts > ? GROUP BY token",
        (since,),
    ).fetchall()
    counts = sorted(r[0] for r in rows)
    _base_cache = (time.time(), {"counts": counts})
    return _base_cache[1]


def smart_money_summary(mint: str, days: int | None = None) -> SmartMoneySummary | None:
    days = days or config.SM_WINDOW_DAYS
    conn = _connect()
    if conn is None:
        return None
    now = int(time.time())
    since = now - days * 86_400
    since_24 = now - 86_400
    s = SmartMoneySummary(window_days=days)
    try:
        with _lock:
            row = conn.execute(
                """SELECT COUNT(*), COUNT(DISTINCT maker),
                          COALESCE(SUM(CASE WHEN sisi='buy'  THEN amount_usd END),0),
                          COALESCE(SUM(CASE WHEN sisi='sell' THEN amount_usd END),0),
                          MIN(ts), MAX(ts)
                   FROM kejadian_sm WHERE token=? AND ts>?""",
                (mint, since),
            ).fetchone()
            row24 = conn.execute(
                """SELECT COUNT(*), COUNT(DISTINCT maker),
                          COALESCE(SUM(CASE WHEN sisi='buy'  THEN amount_usd END),0),
                          COALESCE(SUM(CASE WHEN sisi='sell' THEN amount_usd END),0)
                   FROM kejadian_sm WHERE token=? AND ts>?""",
                (mint, since_24),
            ).fetchone()
            feed = conn.execute("SELECT MAX(ts) FROM kejadian_sm").fetchone()
            tops = conn.execute(
                """SELECT maker,
                          COALESCE(SUM(CASE WHEN sisi='buy' THEN amount_usd ELSE -amount_usd END),0) AS net,
                          MAX(tag)
                   FROM kejadian_sm WHERE token=? AND ts>?
                   GROUP BY maker ORDER BY ABS(net) DESC LIMIT 5""",
                (mint, since),
            ).fetchall()
            base = _baseline(conn, since)
    except sqlite3.Error:
        log.warning("Query smart money gagal untuk %s", mint[:8], exc_info=True)
        return None

    s.events, s.makers, s.buy_usd, s.sell_usd, s.first_ts, s.last_ts = row
    s.events_24h, s.makers_24h, s.buy_usd_24h, s.sell_usd_24h = row24
    s.feed_last_event_ts = feed[0] if feed else None
    # Feed sehat = ada kejadian dalam 1 jam terakhir (ambang yang sama dengan
    # penjaga jaga.js di trench). Tanpa ini, "absen" cuma berarti feed mati.
    s.feed_healthy = bool(s.feed_last_event_ts and now - s.feed_last_event_ts < 3600)
    s.top_makers = [(m, float(n), t or "") for m, n, t in tops]

    counts = base.get("counts") or []
    s.tokens_touched_in_window = len(counts)
    if counts and s.makers > 0:
        below = sum(1 for c in counts if c < s.makers)
        s.makers_percentile = below / len(counts) * 100.0
    return s

"""Rekaman jumlah holder dari waktu ke waktu.

Kenapa perlu: Jupiter cuma menyediakan jendela 5m, 1j, 6j, dan 24j. Tidak ada
jendela 4 jam, dan RugCheck tidak punya deret waktu holder sama sekali. Satu-
satunya cara jujur mendapat angka 4 jam adalah merekamnya sendiri — bukan
menginterpolasi dari 6 jam, karena itu mengarang angka yang kelihatan presisi.

Tiap token yang dicek disimpan snapshot-nya. Begitu ada snapshot berumur cukup,
perubahan untuk jendela apa pun bisa dihitung dari data asli. Sampai riwayatnya
terkumpul, laporannya bilang terus terang bahwa datanya belum ada.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

log = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "history.db")
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# Snapshot lebih tua dari ini dibuang — kita cuma butuh sampai 24 jam ke belakang
RETENTION_SECONDS = 3 * 86_400


def _connect() -> sqlite3.Connection | None:
    global _conn
    if _conn is not None:
        return _conn
    try:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS holder_snapshots (
                   mint    TEXT    NOT NULL,
                   ts      INTEGER NOT NULL,
                   holders INTEGER NOT NULL,
                   PRIMARY KEY (mint, ts)
               )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mint_ts ON holder_snapshots (mint, ts)"
        )
        conn.commit()
        _conn = conn
    except sqlite3.Error:
        log.warning("Gagal membuka database riwayat holder", exc_info=True)
        return None
    return _conn


def record(mint: str, holders: int | None) -> None:
    """Simpan satu snapshot. Diamkan kalau gagal — ini fitur pelengkap."""
    if not mint or not holders or holders <= 0:
        return
    conn = _connect()
    if conn is None:
        return
    now = int(time.time())
    try:
        with _lock:
            # Jangan menumpuk snapshot yang berdempetan (mis. refresh beruntun)
            last = conn.execute(
                "SELECT ts FROM holder_snapshots WHERE mint=? ORDER BY ts DESC LIMIT 1",
                (mint,),
            ).fetchone()
            if last and now - last[0] < 60:
                return
            conn.execute(
                "INSERT OR REPLACE INTO holder_snapshots (mint, ts, holders) VALUES (?,?,?)",
                (mint, now, int(holders)),
            )
            conn.execute(
                "DELETE FROM holder_snapshots WHERE ts < ?", (now - RETENTION_SECONDS,)
            )
            conn.commit()
    except sqlite3.Error:
        log.warning("Gagal menyimpan snapshot holder", exc_info=True)


def change_over(
    mint: str,
    seconds: int,
    current: int | None,
    tolerance: float = 0.4,
) -> tuple[float, int] | None:
    """Perubahan holder (%) dibanding ~`seconds` yang lalu.

    Return (persen_perubahan, umur_baseline_detik), atau None kalau belum ada
    snapshot yang cukup tua. `tolerance` = seberapa jauh umur baseline boleh
    meleset dari target (0.4 = boleh 60%..140% dari target).

    Umur baseline ikut dikembalikan supaya laporan bisa jujur menyebut angka
    ini dibanding kapan — bukan pura-pura tepat 4 jam.
    """
    if not current or current <= 0:
        return None
    conn = _connect()
    if conn is None:
        return None

    now = int(time.time())
    target = now - seconds
    lo = now - int(seconds * (1 + tolerance))
    hi = now - int(seconds * (1 - tolerance))

    try:
        with _lock:
            row = conn.execute(
                """SELECT ts, holders FROM holder_snapshots
                   WHERE mint=? AND ts BETWEEN ? AND ?
                   ORDER BY ABS(ts - ?) LIMIT 1""",
                (mint, lo, hi, target),
            ).fetchone()
    except sqlite3.Error:
        return None

    if not row:
        return None
    ts, past = row
    if not past or past <= 0:
        return None
    return ((current - past) / past * 100.0, now - ts)


def snapshot_count(mint: str) -> int:
    conn = _connect()
    if conn is None:
        return 0
    try:
        with _lock:
            row = conn.execute(
                "SELECT COUNT(*) FROM holder_snapshots WHERE mint=?", (mint,)
            ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0

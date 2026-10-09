"""Riwayat lokal: snapshot pasar + jurnal callout (timestamp) + forward return.

Dua alasan modul ini ada:

1. Jendela waktu yang sumbernya tidak punya. Jupiter cuma 5m/1j/6j/24j untuk
   holder, dan TIDAK ADA sumber gratis untuk deret waktu likuiditas. Satu-satunya
   cara jujur = rekam sendiri tiap token dicek, dan bilang terus terang kalau
   riwayatnya belum ada — bukan interpolasi yang kelihatan presisi.

2. "Wajib Bisa Diuji" (mage) / pre-registrasi (user). Tiap cek adalah callout
   dengan timestamp. Harga dipantau 24 jam ke depan, lalu dihitung forward
   return +1j/+6j/+24j dan MAE (max adverse excursion). Tanpa MAE, putusan
   yang "akhirnya naik" tapi sempat −60% kelihatan seperti kemenangan.
   /hasil menampilkan kalibrasi putusan bot ini terhadap pasar sungguhan.

Tabel:
  snapshots  (mint, ts, price, liq, holders, vol24, mcap)   sampel pasar
  checks     (id, mint, ts, user_id, price, mcap, liq, risk_score, label, flags,
              fwd_1h, fwd_6h, fwd_24h, mae_24h, mfe_24h, done)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import statistics
import threading
import time

import config

from .models import Callout

log = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "history.db")
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# Snapshot lebih tua dari ini dibuang. Butuh >24 jam untuk MAE; 3 hari aman.
RETENTION_SECONDS = 3 * 86_400


def _connect() -> sqlite3.Connection | None:
    global _conn
    if _conn is not None:
        return _conn
    try:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS holder_snapshots (
                mint TEXT NOT NULL, ts INTEGER NOT NULL, holders INTEGER NOT NULL,
                PRIMARY KEY (mint, ts));
            CREATE TABLE IF NOT EXISTS snapshots (
                mint    TEXT    NOT NULL,
                ts      INTEGER NOT NULL,
                price   REAL,
                liq     REAL,
                holders INTEGER,
                vol24   REAL,
                mcap    REAL,
                PRIMARY KEY (mint, ts));
            CREATE INDEX IF NOT EXISTS idx_snap_mint_ts ON snapshots (mint, ts);
            CREATE TABLE IF NOT EXISTS checks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                mint       TEXT    NOT NULL,
                ts         INTEGER NOT NULL,
                user_id    INTEGER,
                price      REAL,
                mcap       REAL,
                liq        REAL,
                risk_score REAL,
                label      TEXT,
                flags      TEXT,
                fwd_1h     REAL, fwd_6h REAL, fwd_24h REAL,
                mae_24h    REAL, mfe_24h REAL,
                done       INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_checks_mint ON checks (mint, ts);
            CREATE INDEX IF NOT EXISTS idx_checks_done ON checks (done, ts);
            """
        )
        # Migrasi satu kali: riwayat holder lama ikut masuk tabel baru
        conn.execute(
            """INSERT OR IGNORE INTO snapshots (mint, ts, holders)
               SELECT mint, ts, holders FROM holder_snapshots"""
        )
        conn.commit()
        _conn = conn
    except sqlite3.Error:
        log.warning("Gagal membuka database riwayat", exc_info=True)
        return None
    return _conn


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
def record(mint: str, holders: int | None, *, price: float | None = None,
           liq: float | None = None, vol24: float | None = None,
           mcap: float | None = None) -> None:
    """Simpan satu snapshot. Diamkan kalau gagal — ini fitur pelengkap."""
    if not mint or (not holders and price is None):
        return
    conn = _connect()
    if conn is None:
        return
    now = int(time.time())
    try:
        with _lock:
            last = conn.execute(
                "SELECT ts FROM snapshots WHERE mint=? ORDER BY ts DESC LIMIT 1", (mint,)
            ).fetchone()
            if last and now - last[0] < 60:
                return
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (mint, ts, price, liq, holders, vol24, mcap) "
                "VALUES (?,?,?,?,?,?,?)",
                (mint, now, price, liq, int(holders) if holders else None, vol24, mcap),
            )
            conn.execute("DELETE FROM snapshots WHERE ts < ?", (now - RETENTION_SECONDS,))
            conn.execute("DELETE FROM holder_snapshots WHERE ts < ?", (now - RETENTION_SECONDS,))
            conn.commit()
    except sqlite3.Error:
        log.warning("Gagal menyimpan snapshot", exc_info=True)


def _past_value(mint: str, column: str, seconds: int, tolerance: float) -> tuple[float, int] | None:
    conn = _connect()
    if conn is None:
        return None
    now = int(time.time())
    target = now - seconds
    lo, hi = now - int(seconds * (1 + tolerance)), now - int(seconds * (1 - tolerance))
    try:
        with _lock:
            row = conn.execute(
                f"""SELECT ts, {column} FROM snapshots
                    WHERE mint=? AND {column} IS NOT NULL AND ts BETWEEN ? AND ?
                    ORDER BY ABS(ts - ?) LIMIT 1""",
                (mint, lo, hi, target),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[1] or row[1] <= 0:
        return None
    return float(row[1]), now - row[0]


def change_over(mint: str, seconds: int, current: int | None, tolerance: float = 0.4) -> tuple[float, int] | None:
    """Perubahan holder (%) dibanding ~`seconds` lalu. Return (persen, umur_baseline)."""
    if not current or current <= 0:
        return None
    got = _past_value(mint, "holders", seconds, tolerance)
    if not got:
        return None
    past, age = got
    return (current - past) / past * 100.0, age


def liq_change_over(mint: str, seconds: int, current: float | None, tolerance: float = 0.4) -> float | None:
    if not current or current <= 0:
        return None
    got = _past_value(mint, "liq", seconds, tolerance)
    return None if not got else (current - got[0]) / got[0] * 100.0


def snapshot_count(mint: str) -> int:
    conn = _connect()
    if conn is None:
        return 0
    try:
        with _lock:
            row = conn.execute("SELECT COUNT(*) FROM snapshots WHERE mint=?", (mint,)).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


# --------------------------------------------------------------------------- #
# Jurnal callout
# --------------------------------------------------------------------------- #
def log_check(mint: str, *, user_id: int | None, price: float | None, mcap: float | None,
              liq: float | None, risk_score: float | None, label: str, flags: list[str]) -> int | None:
    conn = _connect()
    if conn is None:
        return None
    try:
        with _lock:
            cur = conn.execute(
                "INSERT INTO checks (mint, ts, user_id, price, mcap, liq, risk_score, label, flags) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (mint, int(time.time()), user_id, price, mcap, liq, risk_score, label, json.dumps(flags)),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.Error:
        log.warning("Gagal mencatat callout", exc_info=True)
        return None


def _row_to_callout(r: tuple) -> Callout:
    return Callout(id=r[0], ts=r[1], price=r[2], risk_score=r[3], label=r[4] or "",
                   fwd_1h=r[5], fwd_6h=r[6], fwd_24h=r[7], mae_24h=r[8], mfe_24h=r[9], done=bool(r[10]))


def first_callout(mint: str, exclude_id: int | None = None) -> Callout | None:
    """Cek PERTAMA token ini — timestamp acuan 'sejak lo pertama lihat'."""
    conn = _connect()
    if conn is None:
        return None
    try:
        with _lock:
            r = conn.execute(
                """SELECT id, ts, price, risk_score, label, fwd_1h, fwd_6h, fwd_24h, mae_24h, mfe_24h, done
                   FROM checks WHERE mint=? AND id != COALESCE(?, -1) ORDER BY ts ASC LIMIT 1""",
                (mint, exclude_id),
            ).fetchone()
    except sqlite3.Error:
        return None
    return _row_to_callout(r) if r else None


def path_since(mint: str, since_ts: int) -> tuple[float | None, float | None, int]:
    """(harga terendah, harga tertinggi, jumlah sampel) sejak `since_ts` — untuk MAE/MFE live."""
    conn = _connect()
    if conn is None:
        return None, None, 0
    try:
        with _lock:
            r = conn.execute(
                "SELECT MIN(price), MAX(price), COUNT(price) FROM snapshots WHERE mint=? AND ts>=? AND price>0",
                (mint, since_ts),
            ).fetchone()
    except sqlite3.Error:
        return None, None, 0
    return (r[0], r[1], int(r[2] or 0)) if r else (None, None, 0)


def pending_mints() -> list[str]:
    """Token yang masih dalam jendela pantau 24 jam."""
    conn = _connect()
    if conn is None:
        return []
    since = int(time.time()) - config.TRACK_HORIZON_SECONDS
    try:
        with _lock:
            rows = conn.execute(
                "SELECT DISTINCT mint FROM checks WHERE done=0 AND ts>? AND price IS NOT NULL", (since,)
            ).fetchall()
    except sqlite3.Error:
        return []
    return [r[0] for r in rows]


def add_samples(samples: dict[str, tuple[float | None, float | None]]) -> None:
    """Sampel harga/likuiditas dari pemantau latar. {mint: (price, liq)}"""
    conn = _connect()
    if conn is None or not samples:
        return
    now = int(time.time())
    try:
        with _lock:
            conn.executemany(
                "INSERT OR REPLACE INTO snapshots (mint, ts, price, liq) VALUES (?,?,?,?)",
                [(m, now, p, l) for m, (p, l) in samples.items()],
            )
            conn.commit()
    except sqlite3.Error:
        log.warning("Gagal menyimpan sampel", exc_info=True)


def finalize_due() -> int:
    """Hitung fwd return + MAE/MFE untuk callout yang sudah lewat 24 jam. Return jumlah."""
    conn = _connect()
    if conn is None:
        return 0
    now = int(time.time())
    n = 0
    try:
        with _lock:
            due = conn.execute(
                "SELECT id, mint, ts, price FROM checks WHERE done=0 AND ts<=? AND price>0",
                (now - 24 * 3600 - 300,),
            ).fetchall()
            for cid, mint, ts, p0 in due:
                vals = {}
                for lbl, sec in (("fwd_1h", 3600), ("fwd_6h", 6 * 3600), ("fwd_24h", 24 * 3600)):
                    r = conn.execute(
                        """SELECT price FROM snapshots WHERE mint=? AND price>0
                           AND ts BETWEEN ? AND ? ORDER BY ABS(ts-?) LIMIT 1""",
                        (mint, ts + sec - 1200, ts + sec + 1200, ts + sec),
                    ).fetchone()
                    vals[lbl] = (r[0] / p0 - 1) * 100 if r else None
                r = conn.execute(
                    "SELECT MIN(price), MAX(price) FROM snapshots WHERE mint=? AND price>0 AND ts BETWEEN ? AND ?",
                    (mint, ts, ts + 24 * 3600),
                ).fetchone()
                mae = (r[0] / p0 - 1) * 100 if r and r[0] else None
                mfe = (r[1] / p0 - 1) * 100 if r and r[1] else None
                conn.execute(
                    "UPDATE checks SET fwd_1h=?, fwd_6h=?, fwd_24h=?, mae_24h=?, mfe_24h=?, done=1 WHERE id=?",
                    (vals["fwd_1h"], vals["fwd_6h"], vals["fwd_24h"], mae, mfe, cid),
                )
                n += 1
            if n:
                conn.commit()
    except sqlite3.Error:
        log.warning("Finalisasi callout gagal", exc_info=True)
    return n


def calibration() -> dict:
    """Ringkasan nilai prediktif putusan & flag. Dipakai /hasil."""
    conn = _connect()
    out: dict = {"total": 0, "done": 0, "by_label": [], "by_flag": []}
    if conn is None:
        return out
    try:
        with _lock:
            out["total"] = conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
            rows = conn.execute(
                "SELECT label, flags, fwd_24h, mae_24h, fwd_1h FROM checks WHERE done=1 AND fwd_24h IS NOT NULL"
            ).fetchall()
    except sqlite3.Error:
        return out
    out["done"] = len(rows)

    def summarise(group: list[tuple]) -> dict:
        f24 = [r[2] for r in group]
        mae = [r[3] for r in group if r[3] is not None]
        f1 = [r[4] for r in group if r[4] is not None]
        return {
            "n": len(group),
            "med_24h": statistics.median(f24) if f24 else None,
            "win_24h": sum(1 for x in f24 if x > 0) / len(f24) * 100 if f24 else None,
            "med_mae": statistics.median(mae) if mae else None,
            "med_1h": statistics.median(f1) if f1 else None,
        }

    labels: dict[str, list] = {}
    flags: dict[str, list] = {}
    for r in rows:
        labels.setdefault(r[0] or "?", []).append(r)
        try:
            for f in json.loads(r[1] or "[]"):
                flags.setdefault(f, []).append(r)
        except ValueError:
            pass
    out["by_label"] = [(k, summarise(v)) for k, v in sorted(labels.items(), key=lambda kv: -len(kv[1]))]
    out["by_flag"] = [(k, summarise(v)) for k, v in sorted(flags.items(), key=lambda kv: -len(kv[1])) if len(v) >= 3]
    return out

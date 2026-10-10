"""Konfigurasi terpusat, dibaca dari environment / .env."""
import os
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

_raw_allowed = (os.getenv("ALLOWED_USER_IDS") or "").strip()
ALLOWED_USER_IDS = {
    int(x) for x in _raw_allowed.replace(" ", "").split(",") if x.isdigit()
}

SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com"

MIN_LIQUIDITY_USD = _float("MIN_LIQUIDITY_USD", 3000.0)
CACHE_TTL = _int("CACHE_TTL", 90)

# Berapa banyak top holder yang dicek aktivitasnya on-chain
TOP_HOLDER_COUNT = 10

# Batas request RPC paralel — public RPC gampang kena 429
RPC_CONCURRENCY = 4

# Berapa transaksi per holder yang diperiksa untuk menentukan arah beli/jual.
# Tiap transaksi = 1 panggilan getTransaction (dibatch), jadi angka besar
# bikin analisa lambat dan rawan kena rate limit di public RPC.
# Set 0 untuk mematikan analisa arah aliran sepenuhnya.
FLOW_TX_PER_HOLDER = _int("FLOW_TX_PER_HOLDER", 4)

# Public RPC menolak batch getTransaction yang besar (diukur: 3 lolos,
# 5 sebagian ditolak, 10 ditolak semua). Naikkan kalau pakai Helius/QuickNode.
RPC_BATCH_SIZE = _int("RPC_BATCH_SIZE", 3)
RPC_BATCH_PACE = _float("RPC_BATCH_PACE", 0.12)  # jeda antar batch, detik

# Versi transaksi tertinggi yang kita akui ke RPC. Ini BUKAN setelan kosmetik:
# kalau terlalu rendah, RPC membalas HTTP 200 + error -32015 ("Transaction
# version (N) is not supported by the requesting client") per transaksi, dan
# transaksi itu hilang dari analisa arah aliran. Diukur 10 Okt 2026 di Helius:
# dengan nilai 0, hanya 4 dari 20 transaksi terbaca (yang legacy) — justru
# swap DEX (pakai address lookup table, versi 0/1) yang terbuang semua.
# Parser kita membaca meta.pre/postTokenBalances lewat field `owner`, yang
# bentuknya sama di semua versi, jadi nilai tinggi aman.
RPC_MAX_TX_VERSION = _int("RPC_MAX_TX_VERSION", 128)

# Anggaran waktu keras untuk analisa arah beli/jual. Di public RPC yang
# di-throttle, retry bisa berlarut sampai puluhan detik dan bikin bot terasa
# menggantung. Lewat batas ini, analisa berhenti dan holder yang belum terbaca
# dilaporkan "?" — lebih baik sebagian yang jujur daripada nunggu lama.
FLOW_BUDGET_SECONDS = _float("FLOW_BUDGET_SECONDS", 12.0)

# Ambang deteksi cluster "bundle". Pembedanya kepadatan, bukan total:
# bundle sniper = sedikit wallet, porsi per wallet besar.
BUNDLE_MAX_WALLETS = _int("BUNDLE_MAX_WALLETS", 300)
BUNDLE_MIN_PCT_PER_WALLET = _float("BUNDLE_MIN_PCT_PER_WALLET", 0.05)
BUNDLE_MIN_TOTAL_PCT = _float("BUNDLE_MIN_TOTAL_PCT", 1.0)

HTTP_TIMEOUT = 25.0

# ── Lapisan "mage" (divergence framework, Okt 2026) ───────────────────────
# GMGN OpenAPI. Rate limit PER IP (bukan per key) ~1 req/detik, dan IP ini
# juga dipakai trench-kolektor (1 req/20 detik). Jeda minimum di sini sengaja
# lebih longgar dari 1 detik supaya dua proses tidak saling bikin banned.
GMGN_API_KEY = (os.getenv("GMGN_API_KEY") or "").strip()
GMGN_HOST = "https://openapi.gmgn.ai"
GMGN_MIN_INTERVAL = _float("GMGN_MIN_INTERVAL", 1.5)
# Berapa halaman kline harian (100 lilin/halaman) yang boleh ditarik untuk
# TokenMemory. 3 halaman = 300 hari = ~4,5 detik di jeda 1,5 s.
GMGN_KLINE_PAGES_MAX = _int("GMGN_KLINE_PAGES_MAX", 3)

# Database Tahap 0 proyek trench (read-only). Berisi aliran beli/jual wallet
# smart money dari feed GMGN sejak 6 Okt 2026 — sumber lokal untuk D4
# "smart money absence" tanpa satu pun panggilan API.
TRENCH_DB_PATH = os.getenv("TRENCH_DB_PATH") or "/root/trench/data/tahap0.sqlite"
SM_WINDOW_DAYS = _int("SM_WINDOW_DAYS", 7)

# Jurnal callout-sebagai-timestamp: tiap cek dicatat, harga dipantau 24 jam
# ke depan untuk forward return + MAE. Interval sampling, detik.
TRACK_INTERVAL = _int("TRACK_INTERVAL", 600)
TRACK_HORIZON_SECONDS = 24 * 3600 + 15 * 60

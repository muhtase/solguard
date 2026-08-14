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

"""Tentukan arah aliran token tiap top holder: dia AKUMULASI atau DISTRIBUSI.

Cara kerja: ambil beberapa transaksi terakhir yang menyentuh token account
holder, lalu hitung selisih saldo token sebelum vs sesudah tiap transaksi.
  delta > 0  -> token MASUK  (beli / terima)
  delta < 0  -> token KELUAR (jual / kirim)

Kenapa dicocokkan lewat (owner, mint) dan bukan accountIndex:
`preTokenBalances`/`postTokenBalances` menyimpan accountIndex, dan untuk
transaksi versi 0 daftar akun lengkapnya = accountKeys + meta.loadedAddresses
(Address Lookup Table). Swap lewat Jupiter hampir selalu pakai LUT, jadi
mapping index gampang meleset. Field `owner` + `mint` ada langsung di entri
saldo dan kebal terhadap masalah itu.

Batasan jujur: ini mengukur token MASUK/KELUAR dari wallet, bukan konfirmasi
trade di DEX. Transfer antar-wallet sendiri juga terhitung. Untuk niat
"holder ini lagi buang atau nambah", itu justru yang relevan — pindah ke
wallet lain sebelum dump adalah pola yang sama bahayanya.
"""
from __future__ import annotations

import asyncio
import logging
import time

import config

from .models import HolderActivity
from .providers import solrpc

log = logging.getLogger(__name__)


def _amount(entry: dict) -> float | None:
    """Baca jumlah token dari entri balance. uiAmount kadang None."""
    uta = entry.get("uiTokenAmount") or {}
    ui = uta.get("uiAmount")
    if isinstance(ui, (int, float)):
        return float(ui)
    raw = uta.get("amount")
    dec = uta.get("decimals")
    if raw is None or dec is None:
        return None
    try:
        return int(raw) / (10 ** int(dec))
    except (TypeError, ValueError):
        return None


def _balance_for(balances: list | None, owner: str, mint: str) -> float | None:
    """Total saldo mint tsb milik owner di dalam satu sisi transaksi.

    Satu wallet bisa punya lebih dari satu token account untuk mint yang sama,
    jadi entri yang cocok dijumlahkan.
    """
    total = None
    for b in balances or []:
        if not isinstance(b, dict):
            continue
        if b.get("mint") != mint or b.get("owner") != owner:
            continue
        amt = _amount(b)
        if amt is None:
            continue
        total = amt if total is None else total + amt
    return total


def _delta(tx: dict, owner: str, mint: str) -> float | None:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None  # transaksi gagal, tidak mengubah saldo
    pre = _balance_for(meta.get("preTokenBalances"), owner, mint)
    post = _balance_for(meta.get("postTokenBalances"), owner, mint)
    if pre is None and post is None:
        return None
    # Akun baru dibuat di tx ini -> saldo awal 0. Akun ditutup -> saldo akhir 0.
    return (post or 0.0) - (pre or 0.0)


async def annotate_flows(
    holders: list[HolderActivity],
    mint: str,
    *,
    window_days: int = 7,
    max_tx_per_holder: int = config.FLOW_TX_PER_HOLDER,
) -> None:
    """Isi field aliran (buy/sell) tiap holder, di tempat."""
    if not holders or max_tx_per_holder <= 0:
        return

    cutoff = int(time.time()) - window_days * 86_400

    # Kumpulkan signature yang mau diperiksa, dibatasi per holder
    wanted: list[str] = []
    per_holder: dict[str, list[str]] = {}
    for h in holders:
        if h.is_infrastructure:
            continue  # pool/CEX: arus masuk-keluar tidak bermakna sebagai "beli/jual"
        sigs = [
            s
            for s in (h.signatures or [])
            if isinstance(s.get("blockTime"), int)
            and s["blockTime"] >= cutoff
            and s.get("err") is None
        ][:max_tx_per_holder]
        if not sigs:
            continue
        ids = [s["signature"] for s in sigs]
        per_holder[h.token_account] = ids
        wanted.extend(ids)

    if not wanted:
        return

    deadline = asyncio.get_event_loop().time() + config.FLOW_BUDGET_SECONDS
    txs = await solrpc.get_transactions(wanted, deadline=deadline)
    if not txs:
        log.info("Analisa aliran dilewati: RPC tidak mengembalikan transaksi")
        return

    for h in holders:
        ids = per_holder.get(h.token_account)
        if not ids:
            continue
        h.flow_requested = len(ids)
        read = 0   # transaksi yang datanya benar-benar sampai
        moved = 0  # transaksi yang mengubah saldo
        for sig in ids:
            tx = txs.get(sig)
            if not tx:
                continue  # gagal diambil — JANGAN dihitung sebagai "tidak gerak"
            read += 1
            d = _delta(tx, h.owner, mint)
            if d is None or d == 0:
                continue
            moved += 1
            if d > 0:
                h.flow_in += 1
                h.flow_in_amount += d
            else:
                h.flow_out += 1
                h.flow_out_amount += -d
        h.flow_sampled = read
        # Data dianggap sah kalau minimal satu transaksi berhasil dibaca,
        # walau ternyata saldonya tidak berubah (itu info valid: "flat").
        h.flow_ok = read > 0
        h.flow_moved = moved


def net_flow_pct(h: HolderActivity) -> float | None:
    """Perubahan bersih relatif terhadap saldo holder saat ini (%).

    Positif = dia menambah posisi. Negatif = dia mengurangi.
    """
    if not h.flow_ok or not h.flow_moved:
        return None
    net = h.flow_in_amount - h.flow_out_amount
    base = h.ui_amount
    if base <= 0:
        # Saldo sekarang nol tapi ada arus keluar -> dia sudah keluar total
        return -100.0 if h.flow_out_amount > 0 else None
    prev = base - net  # perkiraan saldo sebelum periode sampel
    if prev <= 0:
        # Posisi baru dibuka di dalam jendela waktu — bukan "+100%", tapi baru
        return 999.0 if net > 0 else None
    return max(-100.0, min(999.0, net / prev * 100))


def flow_label(h: HolderActivity) -> str:
    """Label ringkas untuk kolom tabel.

    Bedakan tiga keadaan yang gampang tertukar:
      "—"    tidak dianalisa (pool/CEX, atau tidak ada tx di jendela waktu)
      "?"    seharusnya ada data tapi gagal diambil dari RPC
      "flat" data terbaca, saldonya memang tidak berubah
    """
    if not h.flow_ok:
        return "?" if h.flow_requested else "—"
    if h.flow_in and not h.flow_out:
        return "BELI"
    if h.flow_out and not h.flow_in:
        return "JUAL"
    if h.flow_in_amount > h.flow_out_amount:
        return "net+"
    if h.flow_out_amount > h.flow_in_amount:
        return "net-"
    return "flat"

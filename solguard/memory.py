"""TokenMemory — token dibanding riwayatnya sendiri (D1 mage).

Pertanyaan yang dijawab bukan "sudah turun 90%, murah?" melainkan konteks:
pernah jadi runner atau tidak, seberapa jauh dari puncak, dan apakah pola
7 hari terakhir cocok dengan rantai absorpsi yang mage gambarkan
(volume ambruk -> tekanan jual reda -> harga diam -> cohort baru menyerap).

`resurrection_watch` adalah KANDIDAT FITUR, bukan sinyal. mage sendiri menulis
fitur-fitur ini "lebih cocok sebagai feature untuk backtest". Nilai prediktifnya
baru bisa dilihat dari jurnal (/hasil) setelah cukup banyak cek.
"""
from __future__ import annotations

import time

from .models import TokenMemory, TokenReport

RUNNER_MCAP_USD = 500_000.0     # ambang yang sama dengan kohort R trench
DRAWDOWN_WATCH = -80.0          # mage: jatuh 80–95% dari puncak


def build(rep: TokenReport, candles: list[dict]) -> TokenMemory:
    m = TokenMemory()
    if not candles:
        return m

    now = int(time.time())
    m.days_covered = len(candles)

    ath = max(candles, key=lambda c: c["high"])
    m.ath_price = ath["high"] or None
    m.ath_ts = int(ath["time"] / 1000)
    if m.ath_ts:
        m.days_since_ath = max(0, (now - m.ath_ts) // 86_400)

    price = rep.price_usd
    if price and m.ath_price and m.ath_price > 0:
        m.drawdown_pct = (price / m.ath_price - 1.0) * 100.0
        mc = rep.market_cap or rep.fdv
        if mc and mc > 0:
            supply = mc / price
            m.ath_mcap = m.ath_price * supply
            m.was_runner = m.ath_mcap >= RUNNER_MCAP_USD

    last7 = candles[-7:]
    prior30 = candles[-37:-7]
    if last7:
        m.vol_7d_avg = sum(c["volume"] for c in last7) / len(last7)
        lo = min(c["low"] for c in last7 if c["low"] > 0) if any(c["low"] > 0 for c in last7) else 0
        hi = max(c["high"] for c in last7)
        if lo > 0:
            m.range_7d_pct = (hi - lo) / lo * 100.0
        first_open = last7[0]["open"]
        if first_open > 0 and price:
            m.price_7d_pct = (price / first_open - 1.0) * 100.0
    if prior30:
        m.vol_prior_30d_avg = sum(c["volume"] for c in prior30) / len(prior30)
        if m.vol_prior_30d_avg > 0 and m.vol_7d_avg is not None:
            m.vol_collapse_ratio = m.vol_7d_avg / m.vol_prior_30d_avg

    _assess(m, rep)
    return m


def _assess(m: TokenMemory, rep: TokenReport) -> None:
    """Rantai D1: runner lama -> drawdown dalam -> volume ambruk -> harga diam ->
    holder tidak lagi turun. Semua syarat ditulis supaya alasan gagalnya terbaca."""
    r = m.reasons
    if not m.was_runner:
        if m.ath_mcap is not None:
            r.append(f"puncak mcap cuma ${m.ath_mcap:,.0f} dalam {m.days_covered} hari — bukan mantan runner")
        else:
            r.append("mcap puncak tidak bisa dihitung")
        return
    ok = True
    if m.drawdown_pct is None or m.drawdown_pct > DRAWDOWN_WATCH:
        r.append(f"drawdown {m.drawdown_pct:+.0f}% belum sedalam ambang −80%" if m.drawdown_pct is not None else "drawdown tidak terbaca")
        ok = False
    if m.days_since_ath is not None and m.days_since_ath < 14:
        r.append(f"puncak baru {m.days_since_ath} hari lalu — ini masih fase jatuh, bukan absorpsi")
        ok = False
    if m.vol_collapse_ratio is None:
        r.append("riwayat volume belum 37 hari — fase 'volume ambruk' tak bisa dinilai")
        ok = False
    elif m.vol_collapse_ratio > 0.6:
        r.append(f"volume 7 hari masih {m.vol_collapse_ratio * 100:.0f}% dari rata-rata 30 hari sebelumnya — belum sepi")
        ok = False
    if m.range_7d_pct is not None and m.range_7d_pct > 35:
        r.append(f"harga 7 hari masih liar (rentang {m.range_7d_pct:.0f}%) — belum fase diam")
        ok = False
    if rep.holder_change_24h is not None and rep.holder_change_24h < -1:
        r.append(f"holder masih turun {rep.holder_change_24h:+.1f}%/24j — distribusi belum selesai")
        ok = False
    if ok:
        r.append("mantan runner, drawdown dalam, volume sepi, harga diam, holder tidak lagi turun")
    m.resurrection_watch = ok

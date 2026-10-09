"""Lapisan perilaku (framework divergence mage), DIPISAH dari skor risiko.

Tiga hal dibaca di sini:
  D3  urutan attention -> capital -> price: harga vs holder vs volume vs likuiditas
  D4  smart money hadir atau absen — "siapa yang TIDAK ikut" jadi fitur
  #3  wallet overlap: top holder yang ternyata wallet smart money terpantau

Yang sengaja TIDAK dihitung: angka LAM tunggal. Inputnya ditampilkan apa adanya
di tabel; meringkasnya jadi satu skalar cuma menambah presisi palsu pada
formula yang penulisnya sendiri bilang belum terbukti.

Tidak ada angka di sini yang mengubah skor risiko. Semua bacaan masuk panel
terpisah dan dicatat ke jurnal supaya nilai prediktifnya bisa diuji (/hasil).
"""
from __future__ import annotations

from .models import Behaviour, Divergence, SmartMoneyView, TokenReport
from .providers.trenchdb import SmartMoneySummary

SMART_TAGS = {"smart_degen", "launchpad_smart", "renowned", "top_followed", "kol", "pump_smart"}


# --------------------------------------------------------------------------- #
# D4 — smart money
# --------------------------------------------------------------------------- #
def smart_money(rep: TokenReport, sm: SmartMoneySummary | None, traders: list[dict],
                known_wallets: set[str]) -> SmartMoneyView:
    v = SmartMoneyView()
    if sm is not None:
        v.source_ok = True
        v.feed_healthy = sm.feed_healthy
        v.window_days = sm.window_days
        v.makers_7d, v.events_7d, v.net_usd_7d = sm.makers, sm.events, sm.net_usd
        v.makers_24h, v.net_usd_24h = sm.makers_24h, sm.net_usd_24h
        v.last_ts = sm.last_ts
        v.makers_percentile = sm.makers_percentile
        v.tokens_in_window = sm.tokens_touched_in_window

    if known_wallets and rep.holders:
        v.top_holder_overlap = sum(
            1 for h in rep.holders if not h.is_infrastructure and h.owner in known_wallets
        )

    if traders:
        v.traders_total = len(traders)
        realized = 0.0
        for t in traders:
            rp = t.get("realized_profit")
            try:
                rp = float(rp) if rp is not None else 0.0
            except (TypeError, ValueError):
                rp = 0.0
            realized += rp
            prof = t.get("profit")
            try:
                if prof is not None and float(prof) > 0:
                    v.traders_in_profit += 1
            except (TypeError, ValueError):
                pass
            tags = set(t.get("tags") or [])
            if tags & SMART_TAGS:
                v.traders_smart_tagged += 1
            if t.get("is_suspicious"):
                v.traders_suspicious += 1
            if "fresh_wallet" in tags or t.get("is_new"):
                v.traders_fresh += 1
            nm = t.get("name") or (t.get("native_transfer") or {}).get("name")
            if nm and t.get("exchange") or (nm and nm.lower() in ("binance", "okx", "bybit", "coinbase", "kraken", "kucoin", "gate", "mexc", "bitget")):
                if nm not in v.traders_cex:
                    v.traders_cex.append(nm)
        v.traders_realized_usd = realized

    _read_smart(v, rep)
    return v


def _read_smart(v: SmartMoneyView, rep: TokenReport) -> None:
    price_up = (rep.price_change_24h or 0) >= 30
    retail_up = (rep.holder_change_24h or 0) >= 5
    if not v.source_ok:
        v.status = "unknown"
        v.reading = "DB smart money (trench) tidak tersedia — kehadiran smart money tidak bisa dinilai."
        return
    if not v.feed_healthy:
        v.status = "unknown"
        v.reading = "Feed smart money sedang MATI (>1 jam tanpa kejadian) — 'absen' tidak bermakna sekarang."
        return
    if v.makers_7d == 0:
        if price_up and retail_up:
            v.status = "fomo_divergence"
            v.reading = (
                f"Harga {rep.price_change_24h:+.0f}% & holder {rep.holder_change_24h:+.1f}% dalam 24j, "
                f"tapi NOL wallet smart money terpantau menyentuhnya {v.window_days} hari ini. "
                "Pola yang mage sebut FOMO DIVERGENCE: retail masuk, yang tahu lebih dulu tidak ikut."
            )
        else:
            v.status = "absent"
            v.reading = (
                f"Tidak ada wallet smart money terpantau yang menyentuh token ini {v.window_days} hari terakhir "
                f"(feed mencatat {v.tokens_in_window:,} token lain). Bukan vonis — cuma tidak ada yang tahu lebih dulu di sini."
            )
        return
    v.status = "present"
    arah = "net BELI" if v.net_usd_7d > 0 else "net JUAL"
    pct = f", lebih ramai dari {v.makers_percentile:.0f}% token lain" if v.makers_percentile is not None else ""
    tail = ""
    if v.makers_24h:
        tail = f" 24j terakhir: {v.makers_24h} wallet, {'beli' if v.net_usd_24h > 0 else 'jual'} ${abs(v.net_usd_24h):,.0f}."
    v.reading = (
        f"{v.makers_7d} wallet smart money menyentuh token ini dalam {v.window_days} hari "
        f"({v.events_7d} tx, {arah} ${abs(v.net_usd_7d):,.0f}{pct}).{tail}"
    )


# --------------------------------------------------------------------------- #
# D3 — urutan
# --------------------------------------------------------------------------- #
def divergence(rep: TokenReport, vol: dict, liq_changes: dict[str, float | None], liq_points: int) -> Divergence:
    d = Divergence()
    d.price_1h, d.price_24h = rep.price_change_1h, rep.price_change_24h
    d.price_6h = rep.raw.get("price_change_6h")
    d.holders_1h, d.holders_6h, d.holders_24h = rep.holder_change_1h, rep.holder_change_6h, rep.holder_change_24h
    v1, v6, v24 = (float(vol.get(k) or 0) for k in ("h1", "h6", "h24"))
    if v24 > 0:
        d.vol_accel_1h = v1 * 24 / v24
        d.vol_accel_6h = v6 * 4 / v24
    d.liq_1h, d.liq_6h, d.liq_24h = liq_changes.get("1h"), liq_changes.get("6h"), liq_changes.get("24h")
    d.liq_points = liq_points
    _read_divergence(d)
    return d


def _read_divergence(d: Divergence) -> None:
    p, h, v, l = d.price_24h, d.holders_24h, d.vol_accel_6h, d.liq_24h
    if p is None or h is None:
        d.scenario, d.reading = "unknown", "Data harga/holder 24j tidak lengkap."
        return
    if l is None:
        liq_note = " Likuiditas 24j belum punya riwayat lokal — bacaan ini tanpa kolom likuiditas."
    else:
        liq_note = ""
    if p >= 40 and h >= 5 and (l is None or l < p / 4):
        d.scenario = "attention_led"
        d.reading = ("Harga lari duluan, holder menyusul, likuiditas tertinggal "
                     "— skenario A mage: attention-led, exit makin sempit kalau berbalik." + liq_note)
    elif l is not None and l >= 15 and h >= 3 and abs(p) < 15:
        d.scenario = "capital_led"
        d.reading = ("Likuiditas & holder bertambah tapi harga belum bergerak — skenario B mage: "
                     "capital-led, kandidat delayed repricing. Ini yang dia cari.")
    elif p >= 40 and h < 1:
        d.scenario = "fomo"
        d.reading = "Harga naik tajam tanpa pertumbuhan holder — gerak harga tanpa peserta baru, rawan dibalik." + liq_note
    elif abs(p) < 10 and abs(h) < 1 and (v is None or v < 1.2):
        d.scenario = "quiet"
        d.reading = "Semua datar: harga, holder, volume. Tidak ada divergensi yang bisa dibaca." + liq_note
    else:
        d.scenario = "mixed"
        d.reading = "Tidak ada pola divergensi yang jelas." + liq_note


# --------------------------------------------------------------------------- #
# Sinyal arah (dulu tercampur ke pilar aktivitas) + flag jurnal
# --------------------------------------------------------------------------- #
def directional_notes(rep: TokenReport, b: Behaviour) -> None:
    pos, neg = b.positives, b.negatives
    total_tx = rep.buys_24h + rep.sells_24h
    if total_tx > 0:
        ratio = rep.buys_24h / total_tx
        if ratio < 0.4:
            neg.append(f"Tekanan jual dominan ({rep.buys_24h} buy vs {rep.sells_24h} sell)")
        elif ratio > 0.6:
            pos.append(f"Tekanan beli dominan ({rep.buys_24h} buy vs {rep.sells_24h} sell)")

    graded = [h for h in rep.holders if h.flow_ok and not h.is_infrastructure]
    if graded:
        sellers = sum(1 for h in graded if h.flow_out_amount > h.flow_in_amount)
        buyers = sum(1 for h in graded if h.flow_in_amount > h.flow_out_amount)
        if sellers >= 2 and sellers > buyers:
            neg.append(f"{sellers} dari {len(graded)} top holder sedang mengurangi posisi")
        elif buyers >= 2 and buyers > sellers:
            pos.append(f"{buyers} dari {len(graded)} top holder sedang menambah posisi")

    if rep.holder_change_24h is not None:
        if rep.holder_change_24h < -2:
            neg.append(f"Holder turun {abs(rep.holder_change_24h):.1f}% dalam 24j")
        elif rep.holder_change_24h > 5:
            pos.append(f"Holder naik {rep.holder_change_24h:.1f}% dalam 24j")

    s = b.smart
    if s.status == "present":
        (pos if s.net_usd_7d > 0 else neg).append(
            f"Smart money {s.makers_7d} wallet, {'net beli' if s.net_usd_7d > 0 else 'net jual'} "
            f"${abs(s.net_usd_7d):,.0f} / {s.window_days} hari"
        )
    elif s.status == "fomo_divergence":
        neg.append("FOMO divergence: retail masuk, smart money nol")
    if s.top_holder_overlap:
        pos.append(f"{s.top_holder_overlap} dari top holder adalah wallet smart money terpantau")
    if s.traders_total:
        if s.traders_suspicious >= 10:
            neg.append(f"{s.traders_suspicious} dari {s.traders_total} top trader ditandai mencurigakan oleh GMGN")
        if s.traders_smart_tagged >= 5:
            pos.append(f"{s.traders_smart_tagged} dari {s.traders_total} top trader ber-tag smart money")

    m = b.memory
    if m.resurrection_watch:
        pos.append("Pola D1 resurrection-watch terpenuhi (eksploratori)")

    # flag ringkas untuk jurnal — ini yang nanti diuji nilai prediktifnya
    f = b.flags
    f.append(f"sm:{s.status}")
    f.append(f"d3:{b.divergence.scenario}")
    if m.resurrection_watch:
        f.append("d1:watch")
    elif m.was_runner:
        f.append("d1:runner")
    if s.top_holder_overlap:
        f.append("overlap")

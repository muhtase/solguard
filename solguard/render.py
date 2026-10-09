"""Format TokenReport + Verdict jadi pesan Telegram (parse_mode=HTML)."""
from __future__ import annotations

import html
import time
from datetime import datetime, timedelta, timezone

from .flow import flow_label, net_flow_pct
from .holders import fmt_age
from .models import Behaviour, Callout, TokenReport, Verdict

BAR_FULL = "█"
BAR_EMPTY = "░"


def _esc(s) -> str:
    return html.escape(str(s), quote=False)


def _short(addr: str, head: int = 4, tail: int = 4) -> str:
    if not addr or len(addr) <= head + tail + 1:
        return addr or "—"
    return f"{addr[:head]}…{addr[-tail:]}"


def _money(v: float | None) -> str:
    if v is None:
        return "—"
    v = float(v)
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def _price(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1:
        return f"${v:,.4f}"
    if v >= 0.0001:
        return f"${v:.6f}"
    return f"${v:.10f}".rstrip("0")


def _pct(v: float | None, sign: bool = True) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%" if sign else f"{v:.1f}%"


def _bar(score: float, width: int = 10) -> str:
    filled = int(round(score / 100 * width))
    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def _now_wib() -> str:
    """Waktu sekarang dalam WIB (UTC+7), tidak bergantung zona waktu server."""
    wib = datetime.now(timezone.utc) + timedelta(hours=7)
    return wib.strftime("%d %b %Y %H:%M:%S WIB")


def _age_str(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours < 1:
        return f"{hours * 60:.0f} menit"
    if hours < 48:
        return f"{hours:.0f} jam"
    return f"{hours / 24:.0f} hari"


TELEGRAM_LIMIT = 4096


def render_report(rep: TokenReport, v: Verdict, compact: bool = False) -> str:
    """Susun laporan. `compact` memangkas bagian opsional supaya muat di Telegram.

    Batas pesan Telegram 4096 karakter, dan laporan token dengan banyak temuan
    risiko + banyak cluster bisa mendekatinya. Memotong string mentah berbahaya
    karena bisa membelah tag HTML atau blok <pre>; jadi kalau kepanjangan,
    laporan disusun ulang dengan kuota bagian yang lebih kecil.
    """
    L: list[str] = []
    now = int(time.time())

    n_risks = 3 if compact else 6
    n_clusters = 4 if compact else 8
    n_pos = 4 if compact else 7
    n_neg = 5 if compact else 9

    # ── Header ──────────────────────────────────────────────────────────────
    verified = " ✅" if rep.verified else ""
    L.append(f"<b>{_esc(rep.name)}</b> (<code>${_esc(rep.symbol)}</code>){verified}")
    L.append(f"<code>{_esc(rep.mint)}</code>")
    L.append("")

    # ── Verdict (RISIKO saja — peluang ada di pesan kedua) ──────────────────
    L.append(f"{v.emoji} <b>{_esc(v.label)}</b> — skor aman <b>{v.score}</b>/100")
    L.append(f"<code>{_bar(v.score)}</code>")
    L.append(f"<i>{_esc(v.suggested_size)}</i>")
    L.append("<i>Skor ini = risiko struktur (dirampok/kejebak). Bukan sinyal beli. "
             "Peluang &amp; perilaku dibaca di pesan 🧠 berikutnya.</i>")
    L.append("")

    if v.hard_fails:
        L.append("🚨 <b>CACAT FATAL</b>")
        for f in v.hard_fails:
            L.append(f"  ✖️ {_esc(f)}")
        L.append("")

    # ── Skor per pilar ──────────────────────────────────────────────────────
    names = {
        "security": "Keamanan",
        "distribution": "Distribusi",
        "liquidity": "Likuiditas",
        "activity": "Kualitas tx",
    }
    L.append("📊 <b>RINCIAN SKOR RISIKO</b>")
    for k, lbl in names.items():
        sc = v.pillars.get(k, 0)
        L.append(f"<code>{lbl:<11}{_bar(sc, 8)} {sc:>5.1f}</code>")
    L.append("")

    # ── Market ──────────────────────────────────────────────────────────────
    L.append("💰 <b>MARKET</b>")
    L.append(f"  Harga     : {_price(rep.price_usd)}  ({_pct(rep.price_change_24h)} 24j)")
    L.append(f"  Market Cap: {_money(rep.market_cap)}")
    L.append(f"  Likuiditas: {_money(rep.liquidity_usd)} di {rep.pool_count} pool")
    L.append(f"  Volume 24j: {_money(rep.volume_24h)}")
    L.append(f"  Umur      : {_age_str(rep.age_hours)}")
    if rep.launchpad:
        L.append(f"  Launchpad : {_esc(rep.launchpad)}")
    L.append("")

    # ── Keamanan ────────────────────────────────────────────────────────────
    L.append("🔒 <b>KEAMANAN</b>")
    if rep.authority_data_ok:
        L.append(f"  Mint auth   : {'❌ AKTIF' if rep.mint_authority else '✅ mati'}")
        L.append(f"  Freeze auth : {'❌ AKTIF' if rep.freeze_authority else '✅ mati'}")
    else:
        L.append("  Mint auth   : ❔ tidak terbaca")
        L.append("  Freeze auth : ❔ tidak terbaca")
    if rep.lp_lock_measurable and rep.lp_locked_pct is not None:
        icon = "✅" if rep.lp_locked_pct >= 99 else ("⚠️" if rep.lp_locked_pct >= 50 else "❌")
        L.append(f"  LP terkunci : {icon} {rep.lp_locked_pct:.0f}%")
    elif rep.lp_concentrated_share >= 0.5:
        L.append(
            f"  LP terkunci : ➖ n/a ({rep.lp_concentrated_share * 100:.0f}% likuiditas "
            f"di pool CLMM/DLMM)"
        )
    else:
        L.append("  LP terkunci : ❔ tidak terbaca")
    if rep.metadata_data_ok:
        L.append(f"  Metadata    : {'⚠️ mutable' if rep.mutable_metadata else '✅ immutable'}")
    else:
        L.append("  Metadata    : ❔ tidak terbaca")
    if rep.transfer_fee_pct > 0:
        L.append(f"  Transfer fee: ⚠️ {rep.transfer_fee_pct:.2f}%")
    if rep.rugcheck_risk_score is not None:
        L.append(f"  Risk score  : {rep.rugcheck_risk_score} <i>(makin tinggi makin bahaya)</i>")

    if rep.risks:
        L.append("  <u>Temuan RugCheck:</u>")
        for r in rep.risks[:n_risks]:
            ic = {"danger": "🔴", "warn": "🟡"}.get(r["level"], "🔵")
            L.append(f"    {ic} {_esc(r['name'])}")
    L.append("")

    # ── Distribusi + top 10 ─────────────────────────────────────────────────
    L.append("👥 <b>DISTRIBUSI</b>")
    L.append(f"  Total holder    : {rep.holder_count:,}" if rep.holder_count else "  Total holder    : —")

    # Pertumbuhan holder. 1j/6j/24j dari Jupiter; 4j dari riwayat lokal.
    growth = []
    for lbl, val in (
        ("1j", rep.holder_change_1h),
        ("4j", rep.holder_change_4h),
        ("6j", rep.holder_change_6h),
        ("24j", rep.holder_change_24h),
    ):
        if val is None:
            continue
        # Jangan tampilkan "-0.00%" — itu derau yang terlihat seperti penurunan
        growth.append(f"{lbl} ≈0%" if abs(val) < 0.01 else f"{lbl} {val:+.2f}%")
    if growth:
        L.append(f"  Pertumbuhan     : {' · '.join(growth)}")

    if rep.holder_change_4h is None:
        # Jangan diam-diam menghilangkan kolom yang diminta — jelaskan kenapa
        L.append(
            f"  <i>4j: Jupiter tak punya jendela 4 jam, jadi direkam sendiri "
            f"({rep.holder_history_points} snapshot). Tersedia setelah ~4 jam.</i>"
        )
    elif rep.holder_4h_baseline_age:
        age_h = rep.holder_4h_baseline_age / 3600
        if abs(age_h - 4) > 0.5:
            L.append(f"  <i>angka 4j dibanding snapshot {age_h:.1f} jam lalu</i>")
    L.append(f"  Top 10 (semua)  : {_pct(rep.top10_pct, sign=False)}")
    L.append(f"  Top 10 (non-LP) : {_pct(rep.top10_pct_ex_infra, sign=False)}  ← yang bisa dump")
    if rep.dev_balance_pct is not None:
        L.append(f"  Saldo dev       : {rep.dev_balance_pct:.2f}%")
    if rep.insider_count:
        L.append(f"  Insider di top10: ⚠️ {rep.insider_count}")
    L.append("")

    # ── Cluster / bundle ────────────────────────────────────────────────────
    cl = rep.clusters
    if cl.cluster_count:
        bundles = [c for c in cl.clusters if c.bundle_like]
        L.append("🧬 <b>CLUSTER &amp; BUNDLE</b>")
        L.append(
            f"  Total: <b>{cl.cluster_count} cluster</b> pegang "
            f"<b>{cl.total_pct:.2f}%</b> suplai"
        )
        if bundles:
            L.append(
                f"  ⚠️ Terkoordinasi: <b>{len(bundles)} cluster</b> / "
                f"<b>{cl.bundle_pct:.2f}%</b> suplai / {cl.bundle_wallets} wallet"
            )
        else:
            L.append("  ✅ Tidak ada cluster rapat terdeteksi")
        if cl.creator_pct > 0:
            L.append(f"  Saldo creator: {cl.creator_pct:.2f}%")

        # Tampilkan semua cluster; kalau kebanyakan, sisanya diringkas —
        # tapi jumlah dan persentase totalnya tetap disebut di atas.
        shown = cl.clusters[:n_clusters]
        rest = cl.clusters[n_clusters:]
        L.append("<pre>")
        L.append(f"{'#':<3}{'wallet':>7}{'% suplai':>10}{'%/wallet':>10}  status")
        for i, c in enumerate(shown, start=1):
            tag = "BUNDLE" if c.bundle_like else "menyebar"
            L.append(
                f"{i:<3}{c.size:>7}{c.pct_supply:>9.2f}%{c.per_wallet_pct:>9.3f}%  {tag}"
            )
        if rest:
            rest_pct = sum(c.pct_supply for c in rest)
            rest_w = sum(c.size for c in rest)
            L.append(f"{'…':<3}{rest_w:>7}{rest_pct:>9.2f}%{'':>10}  {len(rest)} cluster lain")
        L.append("</pre>")
        L.append(
            "<i>Yang menentukan bukan total, tapi %/wallet. Cluster besar dengan "
            "%/wallet mungil biasanya cuma graf transfer organik token lama.</i>"
        )
        L.append("")

    if rep.holders:
        L.append("🔍 <b>AKTIVITAS TOP 10 HOLDER</b>")
        L.append("<i>Pergerakan token INI di wallet mereka, bukan aktivitas umum.</i>")
        L.append("<pre>")
        L.append(f"{'#':<3}{'Wallet':<11}{'%':>7}{'arah':>6}{'net':>8}  Terakhir")
        for h in rep.holders:
            flag = ""
            if h.is_infrastructure:
                flag = "🏦"
            elif h.is_locked:
                flag = "🔒"
            elif h.insider:
                flag = "🕵️"
            elif h.pct >= 5:
                flag = "🐋"
            wallet = h.label[:9] if h.label else _short(h.owner, 4, 3)
            last = fmt_age(h.last_active_ts, now) if h.rpc_ok else "?"

            arah = flow_label(h)
            npct = net_flow_pct(h)
            if npct is None:
                net = "—"
            elif npct >= 999:
                net = "new"          # posisi dibuka di dalam jendela waktu
            elif abs(npct) < 0.5:
                net = "~0%"          # jangan tampilkan "-0%", itu membingungkan
            elif abs(npct) < 10:
                net = f"{npct:+.1f}%"
            else:
                net = f"{npct:+.0f}%"
            L.append(
                f"{h.rank:<3}{wallet:<11}{h.pct:>6.2f}%{arah:>6}{net:>8}  {last} {flag}"
            )
        L.append("</pre>")
        L.append("🏦 pool/CEX · 🔒 vesting/locker · 🕵️ insider · 🐋 whale ≥5%")
        L.append(
            "<i>arah = token masuk/keluar wallet 7 hari terakhir "
            "(m=menit, j=jam, hr=hari). "
            "— tidak ada transaksi dalam 7 hari · ? data gagal diambil dari RPC, "
            "bukan berarti diam. "
            "BELI/JUAL bukan konfirmasi trade DEX — transfer antar-wallet ikut "
            "terhitung.</i>"
        )
        L.append("")

    # ── Kesimpulan naratif ──────────────────────────────────────────────────
    if v.positives:
        L.append("✅ <b>POSITIF</b>")
        for p in v.positives[:n_pos]:
            L.append(f"  • {_esc(p)}")
        L.append("")
    if v.negatives:
        L.append("⚠️ <b>CATATAN NEGATIF</b>")
        for n in v.negatives[:n_neg]:
            L.append(f"  • {_esc(n)}")
        L.append("")

    # ── Link ────────────────────────────────────────────────────────────────
    links = [
        f'<a href="https://dexscreener.com/solana/{rep.mint}">DexScreener</a>',
        f'<a href="https://rugcheck.xyz/tokens/{rep.mint}">RugCheck</a>',
        f'<a href="https://solscan.io/token/{rep.mint}">Solscan</a>',
    ]
    for key in ("website", "twitter", "telegram"):
        if key in rep.socials:
            links.append(f'<a href="{_esc(rep.socials[key])}">{key.capitalize()}</a>')
    L.append(" · ".join(links))

    if rep.warnings:
        L.append("")
        for w in rep.warnings:
            L.append(f"<i>⚠️ {_esc(w)}</i>")

    L.append("")
    L.append(f"<i>Data per {_now_wib()} · Bukan saran finansial. DYOR.</i>")

    return "\n".join(L)


# =========================================================================== #
# Pesan kedua: perilaku & konteks (framework mage) — DIPISAH dari risiko
# =========================================================================== #
def _chg(v: float | None, width: int = 7) -> str:
    if v is None:
        return f"{'—':>{width}}"
    if abs(v) >= 1000:
        return f"{v / 1000:+.1f}k%".rjust(width)
    return f"{v:+.0f}%".rjust(width) if abs(v) >= 10 else f"{v:+.1f}%".rjust(width)


def _accel(v: float | None, width: int = 7) -> str:
    return f"{'—':>{width}}" if v is None else f"{v:.1f}x".rjust(width)


def _ago(ts: int | None, now: int) -> str:
    return fmt_age(ts, now) if ts else "—"


def render_behaviour(rep: TokenReport, b: Behaviour, first: Callout | None,
                     path: tuple[float | None, float | None, int] | None,
                     compact: bool = False) -> str:
    L: list[str] = []
    now = int(time.time())
    L.append(f"🧠 <b>PERILAKU &amp; KONTEKS</b> — <code>${_esc(rep.symbol)}</code>")
    L.append("<i>Lapisan terpisah dari skor risiko. Semua ini KANDIDAT fitur yang "
             "nilai prediktifnya diuji lewat /hasil, bukan resep.</i>")
    L.append("")

    # ── Callout sebagai timestamp ───────────────────────────────────────────
    if first and first.price and rep.price_usd:
        chg = (rep.price_usd / first.price - 1) * 100
        L.append("⏱ <b>SEJAK LO PERTAMA CEK</b>")
        L.append(f"  {_ago(first.ts, now)} · harga waktu itu {_price(first.price)} · putusan "
                 f"<i>{_esc(first.label)}</i> ({first.risk_score:.0f})" if first.risk_score is not None
                 else f"  {_ago(first.ts, now)} · harga waktu itu {_price(first.price)}")
        line = f"  Sekarang: <b>{chg:+.1f}%</b>"
        if first.done and first.mae_24h is not None:
            line += f" · 24j pertama: fwd {_chg(first.fwd_24h, 0).strip()}, MAE <b>{first.mae_24h:+.1f}%</b>, MFE {first.mfe_24h:+.1f}%"
        elif path and path[2] >= 2 and path[0]:
            lo, hi, n = path
            L.append(line)
            line = (f"  Jalur sejak itu ({n} sampel): terendah <b>{(lo / first.price - 1) * 100:+.1f}%</b> "
                    f"(MAE sementara) · tertinggi {(hi / first.price - 1) * 100:+.1f}%")
        L.append(line)
        L.append("")

    # ── D1 TokenMemory ──────────────────────────────────────────────────────
    m = b.memory
    L.append("🧬 <b>MEMORI TOKEN</b> <i>(D1)</i>")
    if not m.days_covered:
        L.append("  Riwayat harian tidak terbaca (GMGN kline kosong/nonaktif).")
    else:
        ath = f"{_price(m.ath_price)}"
        if m.ath_mcap:
            ath += f" ≈ mcap {_money(m.ath_mcap)}"
        L.append(f"  Puncak {m.days_covered} hari: {ath}, {m.days_since_ath} hari lalu")
        if m.drawdown_pct is not None:
            L.append(f"  Dari puncak    : <b>{m.drawdown_pct:+.0f}%</b> · "
                     f"{'mantan RUNNER' if m.was_runner else 'bukan mantan runner'}")
        vol = "—"
        if m.vol_collapse_ratio is not None:
            vol = f"{m.vol_collapse_ratio * 100:.0f}% dari rata-rata 30 hari sebelumnya"
        elif m.vol_7d_avg is not None:
            vol = f"{_money(m.vol_7d_avg)}/hari (riwayat <37 hari)"
        L.append(f"  Volume 7 hari  : {vol}")
        if m.range_7d_pct is not None:
            L.append(f"  Rentang 7 hari : {m.range_7d_pct:.0f}% · harga 7h {_chg(m.price_7d_pct, 0).strip()}")
        if m.resurrection_watch:
            L.append("  🔎 <b>RESURRECTION-WATCH</b> — pola absorpsi D1 terpenuhi (eksploratori)")
        if m.reasons and not compact:
            L.append(f"  <i>{_esc(m.reasons[-1] if m.resurrection_watch else '; '.join(m.reasons[:2]))}</i>")
    L.append("")

    # ── D4 Smart money ──────────────────────────────────────────────────────
    sm = b.smart
    icon = {"present": "👀", "absent": "🫥", "fomo_divergence": "🚨", "unknown": "❔"}[sm.status]
    L.append(f"{icon} <b>SMART MONEY</b> <i>(D4)</i>")
    L.append(f"  {_esc(sm.reading)}")
    if sm.status == "present" and sm.last_ts:
        L.append(f"  Terakhir: {_ago(sm.last_ts, now)}")
    if sm.top_holder_overlap:
        L.append(f"  🔗 Overlap: <b>{sm.top_holder_overlap}</b> top holder = wallet smart money terpantau")
    if sm.traders_total:
        prof = f"{sm.traders_in_profit}/{sm.traders_total} untung"
        extra = []
        if sm.traders_smart_tagged:
            extra.append(f"{sm.traders_smart_tagged} ber-tag smart")
        if sm.traders_suspicious:
            extra.append(f"{sm.traders_suspicious} mencurigakan")
        if sm.traders_fresh:
            extra.append(f"{sm.traders_fresh} wallet baru")
        if sm.traders_cex:
            extra.append("CEX: " + ", ".join(sm.traders_cex[:3]))
        L.append(f"  Top trader GMGN: {prof}" + (f" · {' · '.join(extra)}" if extra else ""))
        if sm.traders_realized_usd is not None:
            L.append(f"  PnL realisasi gabungan top 100: <b>{'+' if sm.traders_realized_usd >= 0 else '−'}{_money(abs(sm.traders_realized_usd))}</b>")
    L.append("")

    # ── D3 Urutan ───────────────────────────────────────────────────────────
    d = b.divergence
    L.append("📐 <b>URUTAN ATTENTION → CAPITAL → PRICE</b> <i>(D3)</i>")
    L.append("<pre>")
    L.append(f"{'':<11}{'1j':>7}{'6j':>7}{'24j':>7}")
    L.append(f"{'Harga':<11}{_chg(d.price_1h)}{_chg(d.price_6h)}{_chg(d.price_24h)}")
    L.append(f"{'Holder':<11}{_chg(d.holders_1h)}{_chg(d.holders_6h)}{_chg(d.holders_24h)}")
    L.append(f"{'Volume*':<11}{_accel(d.vol_accel_1h)}{_accel(d.vol_accel_6h)}{'1.0x':>7}")
    L.append(f"{'Likuiditas':<11}{_chg(d.liq_1h)}{_chg(d.liq_6h)}{_chg(d.liq_24h)}")
    L.append("</pre>")
    L.append(f"  <b>{_esc(d.reading)}</b>")
    if not compact:
        note = "*volume = laju jendela itu dibanding laju 24j; >1x = sedang akselerasi."
        if d.liq_24h is None:
            note += f" Likuiditas direkam lokal tiap cek/pantau ({d.liq_points} snapshot) — kolomnya terisi setelah token ini dipantau."
        L.append(f"  <i>{note}</i>")
    L.append("")

    # ── Sinyal arah (dulu tercampur ke skor) ────────────────────────────────
    if b.positives or b.negatives:
        L.append("🧭 <b>SINYAL ARAH</b> <i>(tidak masuk skor risiko)</i>")
        for ptxt in b.positives[: 3 if compact else 6]:
            L.append(f"  ▲ {_esc(ptxt)}")
        for ntxt in b.negatives[: 3 if compact else 6]:
            L.append(f"  ▼ {_esc(ntxt)}")
        L.append("")

    # ── Kesimpulan & verdict ────────────────────────────────────────────────
    c = b.conclusion
    if c.label:
        L.append(f"🎯 <b>KESIMPULAN</b>")
        for ln in c.lines[: 2 if compact else 4]:
            L.append(f"  • {_esc(ln)}")
        L.append(f"{c.emoji} <b>VERDICT: {_esc(c.label)}</b>")
        L.append(f"  <i>{_esc(c.action)}</i>")
        L.append("")

    srcs = rep.raw.get("behaviour_sources") or {}
    off = [k for k, ok in srcs.items() if not ok]
    if off:
        L.append(f"<i>⚠️ Sumber perilaku nonaktif: {', '.join(off)}</i>")
    L.append("<i>Cek ini dicatat sebagai timestamp. Harga dipantau 24 jam → forward return &amp; MAE masuk /hasil. "
             "mage: \"callout itu timestamp, bukan sinyal entry.\"</i>")
    return "\n".join(L)


def render_calibration(cal: dict) -> str:
    """Kalibrasi putusan & flag terhadap pasar (Uji B versi SolGuard)."""
    L = ["📏 <b>KALIBRASI PUTUSAN</b>",
         f"<i>{cal['total']} cek tercatat · {cal['done']} sudah lewat 24 jam &amp; dihitung.</i>", ""]
    if cal["done"] < 5:
        L.append("Belum cukup data. Tiap token yang lo cek dipantau 24 jam; angka di sini "
                 "baru bermakna setelah puluhan cek. Jangan baca pola dari 2–3 titik.")
        return "\n".join(L)

    def tbl(rows: list, title: str) -> None:
        L.append(f"<b>{title}</b>")
        L.append("<pre>")
        L.append(f"{'':<17}{'n':>4}{'fwd1j':>7}{'fwd24j':>8}{'win':>5}{'MAE':>7}")
        for k, s_ in rows:
            win = f"{s_['win_24h']:.0f}%" if s_['win_24h'] is not None else "—"
            L.append(
                f"{k[:16]:<17}{s_['n']:>4}{_chg(s_['med_1h'], 7)}{_chg(s_['med_24h'], 8)}"
                f"{win:>5}{_chg(s_['med_mae'], 7)}"
            )
        L.append("</pre>")

    if cal["by_label"]:
        tbl(cal["by_label"], "Per putusan risiko")
    if cal["by_flag"]:
        tbl(cal["by_flag"], "Per flag perilaku (n ≥ 3)")
    L.append("<i>Median. fwd = return setelah cek; win = % yang positif di 24j; "
             "MAE = drawdown terdalam dalam 24j (median). Flag yang fwd-nya tak beda "
             "dari yang lain setelah biaya+slippage = buang, kata mage sendiri.</i>")
    return "\n".join(L)

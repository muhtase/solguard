"""Format TokenReport + Verdict jadi pesan Telegram (parse_mode=HTML)."""
from __future__ import annotations

import html
import time
from datetime import datetime, timedelta, timezone

from .flow import flow_label, net_flow_pct
from .holders import fmt_age
from .models import TokenReport, Verdict

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

    # ── Verdict ─────────────────────────────────────────────────────────────
    L.append(f"{v.emoji} <b>{_esc(v.label)}</b> — <b>{v.score}</b>/100")
    L.append(f"<code>{_bar(v.score)}</code>")
    L.append(f"<i>{_esc(v.suggested_size)}</i>")
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
        "activity": "Aktivitas",
    }
    L.append("📊 <b>RINCIAN SKOR</b>")
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

"""Gabungkan semua provider jadi satu TokenReport."""
from __future__ import annotations

import asyncio
import logging
import time

from . import clusters, divergence, history, memory
from .flow import annotate_flows
from .holders import build_top_holders, concentration
from .models import Behaviour, TokenReport
from .providers import dexscreener, gmgn, jupiter, rugcheck, trenchdb

log = logging.getLogger(__name__)


async def analyze(mint: str) -> TokenReport:
    rc_task = asyncio.create_task(rugcheck.fetch_report(mint))
    ds_task = asyncio.create_task(dexscreener.fetch_pairs(mint))
    jp_task = asyncio.create_task(jupiter.fetch_token(mint))

    rc, ds, jp = await asyncio.gather(rc_task, ds_task, jp_task, return_exceptions=True)
    rc = rc if isinstance(rc, dict) else None
    ds = ds if isinstance(ds, list) else []
    jp = jp if isinstance(jp, dict) else None

    rep = TokenReport(mint=mint)
    rep.sources_ok = {"rugcheck": rc is not None, "dexscreener": bool(ds), "jupiter": jp is not None}
    # Lapisan perilaku punya sumber sendiri; kegagalannya TIDAK memotong skor
    # risiko (bukan sumber risiko), tapi tetap dilaporkan di panelnya.
    rep.raw["behaviour_sources"] = {"gmgn": gmgn.enabled(), "trench_db": trenchdb.available()}

    if not any(rep.sources_ok.values()):
        rep.warnings.append("Semua sumber data gagal dihubungi.")
        return rep

    _fill_identity(rep, rc, ds, jp)
    _fill_market(rep, ds, jp)
    _fill_activity(rep, ds, jp)
    _fill_security(rep, rc, jp)
    _fill_distribution(rep, rc, jp)

    if rc:
        rep.clusters = clusters.analyze(rc, rep.age_hours)
        rep.holders = await build_top_holders(rc, rep.price_usd)
        if rep.holders:
            # Arah beli/jual butuh RPC tambahan; kalau gagal, laporan tetap jalan
            try:
                await annotate_flows(rep.holders, mint)
            except Exception:
                log.warning("Analisa arah aliran gagal", exc_info=True)

            total, ex_infra = concentration(rep.holders)
            # RugCheck sudah menyediakan agregat top-holder; pakai punya kita
            # karena kita bisa memisahkan pool LP dari wallet asli.
            rep.top10_pct = total
            rep.top10_pct_ex_infra = ex_infra
            rep.insider_count = sum(1 for h in rep.holders if h.insider)
    else:
        rep.warnings.append("RugCheck tidak merespons — data keamanan & holder tidak lengkap.")

    # Jendela 4 jam tidak ada di sumber mana pun, jadi dihitung dari riwayat
    # yang kita rekam sendiri tiap kali token ini dicek. Likuiditas juga.
    history.record(
        mint, rep.holder_count, price=rep.price_usd, liq=rep.liquidity_usd or None,
        vol24=rep.volume_24h or None, mcap=rep.market_cap,
    )
    rep.holder_history_points = history.snapshot_count(mint)
    if rep.holder_count:
        got = history.change_over(mint, 4 * 3600, rep.holder_count)
        if got:
            rep.holder_change_4h, rep.holder_4h_baseline_age = got

    return rep


async def behaviour(rep: TokenReport) -> Behaviour:
    """Lapisan perilaku (mage): TokenMemory, smart money, divergence.

    Dipanggil TERPISAH dari analyze() dan tidak menyentuh skor risiko. Panggilan
    GMGN di sini berurutan (pacing 1 req/1,5 s, berbagi IP dengan trench-kolektor),
    jadi butuh beberapa detik — makanya dikirim sebagai pesan kedua.
    """
    b = Behaviour()
    sm = None
    try:
        sm = trenchdb.smart_money_summary(rep.mint)
    except Exception:
        log.warning("Ringkasan smart money gagal", exc_info=True)
    known = trenchdb.known_smart_wallets() if trenchdb.available() else set()

    traders: list[dict] = []
    candles: list[dict] = []
    if gmgn.enabled():
        try:
            traders = await gmgn.top_traders(rep.mint)
        except Exception:
            log.warning("GMGN top trader gagal", exc_info=True)
        try:
            # Token muda tidak perlu 300 hari lilin; hemat panggilan & waktu
            pages = 1
            if rep.age_hours is not None:
                pages = max(1, min(int(rep.age_hours / 24 / 100) + 1, 99))
            candles = await gmgn.kline_daily(rep.mint, max_pages=min(pages, gmgn.config.GMGN_KLINE_PAGES_MAX))
        except Exception:
            log.warning("GMGN kline gagal", exc_info=True)

    b.memory = memory.build(rep, candles)
    b.smart = divergence.smart_money(rep, sm, traders, known)

    liq = rep.liquidity_usd or None
    liq_changes = {
        "1h": history.liq_change_over(rep.mint, 3600, liq),
        "6h": history.liq_change_over(rep.mint, 6 * 3600, liq),
        "24h": history.liq_change_over(rep.mint, 24 * 3600, liq),
    }
    b.divergence = divergence.divergence(
        rep, rep.raw.get("volume_windows") or {}, liq_changes, rep.holder_history_points
    )
    divergence.directional_notes(rep, b)
    b.flags.append("gmgn:ok" if traders or candles else "gmgn:none")
    return b


def _fill_identity(rep: TokenReport, rc: dict | None, ds: list, jp: dict | None) -> None:
    meta = (rc or {}).get("tokenMeta") or {}
    best = dexscreener.best_pair(ds)
    base = best.get("baseToken") or {}

    rep.name = meta.get("name") or base.get("name") or (jp or {}).get("name") or "?"
    rep.symbol = meta.get("symbol") or base.get("symbol") or (jp or {}).get("symbol") or "?"
    rep.creator = (rc or {}).get("creator") or (jp or {}).get("dev")

    lp = (rc or {}).get("launchpad") or {}
    if isinstance(lp, dict) and lp.get("name"):
        rep.launchpad = lp["name"]
    else:
        dp = (rc or {}).get("deployPlatform")
        # RugCheck mengisi "unknown" untuk token lama — jangan tampilkan itu
        if dp and str(dp).lower() not in ("unknown", "none", ""):
            rep.launchpad = str(dp)

    ver = (rc or {}).get("verification") or {}
    rep.verified = bool(ver.get("jup_verified")) or bool((jp or {}).get("isVerified"))
    if best:
        rep.socials = dexscreener.socials(best)


def _fill_market(rep: TokenReport, ds: list, jp: dict | None) -> None:
    best = dexscreener.best_pair(ds)

    price = best.get("priceUsd")
    try:
        rep.price_usd = float(price) if price is not None else None
    except (TypeError, ValueError):
        rep.price_usd = None
    if rep.price_usd is None and jp:
        rep.price_usd = jp.get("usdPrice")

    rep.market_cap = best.get("marketCap") or (jp or {}).get("mcap")
    rep.fdv = best.get("fdv") or (jp or {}).get("fdv")

    ds_liq = dexscreener.total_liquidity(ds)
    jp_liq = float((jp or {}).get("liquidity") or 0.0)
    # DexScreener menjumlah semua pool; Jupiter kadang lebih update. Ambil yang terbesar.
    rep.liquidity_usd = max(ds_liq, jp_liq)
    rep.pool_count = len(ds)
    rep.main_dex = best.get("dexId")

    created = best.get("pairCreatedAt")
    if not created and jp:
        fp = jp.get("firstPool") or {}
        iso = fp.get("createdAt")
        if iso:
            try:
                from datetime import datetime

                created = int(
                    datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000
                )
            except (ValueError, TypeError):
                created = None
    if created:
        rep.pair_created_at = int(created)
        rep.age_hours = max(0.0, (time.time() - int(created) / 1000) / 3600)


def _fill_activity(rep: TokenReport, ds: list, jp: dict | None) -> None:
    best = dexscreener.best_pair(ds)
    vol = best.get("volume") or {}
    txns = (best.get("txns") or {}).get("h24") or {}
    chg = best.get("priceChange") or {}

    rep.volume_24h = float(vol.get("h24") or 0.0)
    rep.buys_24h = int(txns.get("buys") or 0)
    rep.sells_24h = int(txns.get("sells") or 0)
    rep.price_change_24h = chg.get("h24")
    rep.price_change_1h = chg.get("h1")
    rep.raw["price_change_6h"] = chg.get("h6")
    rep.raw["volume_windows"] = {k: vol.get(k) for k in ("m5", "h1", "h6", "h24")}

    if jp:
        s24 = jupiter.stats(jp, "24h")
        rep.traders_24h = s24.get("numTraders")
        rep.net_buyers_24h = s24.get("numNetBuyers")
        rep.holder_change_24h = s24.get("holderChange")
        rep.holder_change_5m = jupiter.stats(jp, "5m").get("holderChange")
        rep.holder_change_1h = jupiter.stats(jp, "1h").get("holderChange")
        rep.holder_change_6h = jupiter.stats(jp, "6h").get("holderChange")
        rep.organic_score = jp.get("organicScore")
        rep.organic_label = jp.get("organicScoreLabel")
        rep.organic_ratio_24h = jupiter.organic_volume_ratio(jp, "24h")
        # Jupiter menghitung volume gabungan lintas pool — lebih lengkap dari 1 pair
        jv = float(s24.get("buyVolume") or 0) + float(s24.get("sellVolume") or 0)
        if jv > rep.volume_24h:
            rep.volume_24h = jv
        if not rep.buys_24h:
            rep.buys_24h = int(s24.get("numBuys") or 0)
        if not rep.sells_24h:
            rep.sells_24h = int(s24.get("numSells") or 0)


def _fill_security(rep: TokenReport, rc: dict | None, jp: dict | None) -> None:
    if rc:
        rep.mint_authority = rc.get("mintAuthority")
        rep.freeze_authority = rc.get("freezeAuthority")
        rep.mutable_metadata = bool((rc.get("tokenMeta") or {}).get("mutable"))
        rep.authority_data_ok = True
        rep.metadata_data_ok = bool(rc.get("tokenMeta"))
        rep.transfer_fee_pct = rugcheck.transfer_fee_pct(rc)

        lock = rugcheck.lp_lock_status(rc)
        rep.lp_locked_pct = lock["locked_pct"]
        rep.lp_lock_measurable = lock["measurable"]
        rep.lp_lockable_share = lock["lockable_share"]
        rep.lp_concentrated_share = lock["concentrated_share"]

        rep.rugged = bool(rc.get("rugged"))
        rep.rugcheck_risk_score = rc.get("score_normalised")
        rep.risks = rugcheck.risk_items(rc)
    elif jp:
        audit = jp.get("audit") or {}
        # Fallback: Jupiter cuma kasih boolean, bukan alamat authority-nya
        mint_off = audit.get("mintAuthorityDisabled")
        freeze_off = audit.get("freezeAuthorityDisabled")
        if mint_off is False:
            rep.mint_authority = "ACTIVE"
        if freeze_off is False:
            rep.freeze_authority = "ACTIVE"
        rep.authority_data_ok = mint_off is not None and freeze_off is not None


def _fill_distribution(rep: TokenReport, rc: dict | None, jp: dict | None) -> None:
    if rc:
        rep.holder_count = rc.get("totalHolders")
        gi = rc.get("graphInsidersDetected")
        rep.graph_insiders = int(gi or 0) if isinstance(gi, (int, float)) else 0
    if jp:
        if not rep.holder_count:
            rep.holder_count = jp.get("holderCount")
        audit = jp.get("audit") or {}
        rep.dev_balance_pct = audit.get("devBalancePercentage")
        rep.dev_mints = audit.get("devMints")
        if not rep.top10_pct:
            rep.top10_pct = float(audit.get("topHoldersPercentage") or 0.0)

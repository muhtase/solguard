"""Analisa top-10 holder: siapa mereka, dan kapan terakhir mereka gerak."""
from __future__ import annotations

import time

import config

from .models import HolderActivity
from .providers import rugcheck, solrpc


async def build_top_holders(
    report: dict,
    price_usd: float | None,
    limit: int = config.TOP_HOLDER_COUNT,
) -> list[HolderActivity]:
    raw_holders = (report.get("topHolders") or [])[:limit]
    if not raw_holders:
        return []

    holders: list[HolderActivity] = []
    for i, h in enumerate(raw_holders, start=1):
        token_account = h.get("address") or ""
        owner = h.get("owner") or token_account
        try:
            pct = float(h.get("pct") or 0.0)
        except (TypeError, ValueError):
            pct = 0.0
        try:
            ui_amount = float(h.get("uiAmount") or 0.0)
        except (TypeError, ValueError):
            ui_amount = 0.0

        # Label bisa nempel di token account maupun di owner-nya
        name, ltype = rugcheck.known_account_label(report, token_account)
        if not name:
            name, ltype = rugcheck.known_account_label(report, owner)

        holders.append(
            HolderActivity(
                rank=i,
                owner=owner,
                token_account=token_account,
                pct=pct,
                ui_amount=ui_amount,
                usd_value=(ui_amount * price_usd) if price_usd else None,
                insider=bool(h.get("insider")),
                label=name,
                label_type=ltype,
            )
        )

    # Ambil riwayat on-chain token account masing-masing holder
    accounts = [h.token_account for h in holders if h.token_account]
    sigs_map = await solrpc.get_signatures_many(accounts, limit=40)

    now = int(time.time())
    day_ago = now - 86_400
    any_rpc_ok = False

    for h in holders:
        sigs = sigs_map.get(h.token_account) or []
        if not sigs:
            # Bedakan "RPC gagal" dari "memang tidak ada aktivitas" tidak
            # mungkin 100% akurat lewat 1 call; kita tandai lemah dan
            # verifikasi lewat agregat di bawah.
            h.rpc_ok = False
            continue
        any_rpc_ok = True
        h.rpc_ok = True
        h.signatures = sigs
        h.tx_total_seen = len(sigs)
        times = [s.get("blockTime") for s in sigs if isinstance(s.get("blockTime"), int)]
        if times:
            h.last_active_ts = max(times)
        h.tx_24h = sum(1 for t in times if t >= day_ago)

    # Kalau semua holder gagal, kemungkinan besar RPC yang bermasalah,
    # bukan holder-nya yang benar-benar diam.
    if not any_rpc_ok:
        for h in holders:
            h.rpc_ok = False

    return holders


def concentration(holders: list[HolderActivity]) -> tuple[float, float]:
    """Return (top10_pct_total, top10_pct_tanpa_infrastruktur).

    Pool LP dan wallet CEX dikeluarkan karena bukan risiko dump perorangan.
    """
    total = sum(h.pct for h in holders)
    ex_infra = sum(h.pct for h in holders if not h.is_infrastructure)
    return round(total, 2), round(ex_infra, 2)


def fmt_age(ts: int | None, now: int | None = None) -> str:
    """Umur dalam bahasa manusia.

    Satuan hari ditulis "hr", bukan "h", supaya tidak tertukar dengan "j" (jam).
    "228h" gampang dibaca sebagai 228 jam padahal maksudnya 228 hari.
    """
    if not ts:
        return "—"
    now = now or int(time.time())
    d = max(0, now - ts)
    if d < 3600:
        return f"{d // 60}m lalu"
    if d < 86_400:
        return f"{d // 3600}j lalu"
    return f"{d // 86_400}hr lalu"

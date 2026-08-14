"""RugCheck — sumber utama data keamanan kontrak, LP lock, dan top holder.

Catatan penting: `score_normalised` dari RugCheck adalah SKOR RISIKO,
makin TINGGI makin BERBAHAYA (BONK ~7, token scam ~80+).
"""
from __future__ import annotations

from typing import Any

from ..http import get_json

BASE = "https://api.rugcheck.xyz/v1"


async def fetch_report(mint: str) -> dict[str, Any] | None:
    return await get_json(
        f"{BASE}/tokens/{mint}/report",
        cache_key=f"rugcheck:{mint}",
    )


# Market yang punya LP token fungible sungguhan — di sini "LP locked/burned"
# adalah konsep yang valid dan bisa diukur.
LOCKABLE_LP_TYPES = {
    "raydium",
    "meteora",
    "fluxbeam",
    "pump_fun_amm",
    "raydium_cpmm",
}

# Pool concentrated-liquidity: posisi LP berupa NFT dengan range harga, bukan
# token fungible. RugCheck mengisi lpLockedPct = 0 karena TIDAK BERLAKU —
# bukan karena LP-nya bebas ditarik. Menyamakan keduanya = false positive.
CONCENTRATED_TYPES = {
    "orca",
    "raydium_clmm",
    "meteoraDlmm",
    "meteora_damm_v2",
}


def _market_liq(market: dict) -> float:
    lp = market.get("lp") or {}
    try:
        return float(lp.get("quoteUSD") or 0) + float(lp.get("baseUSD") or 0)
    except (TypeError, ValueError):
        return 0.0


def main_market(report: dict) -> dict:
    """Market dengan likuiditas USD terbesar."""
    markets = report.get("markets") or []
    best, best_liq = {}, -1.0
    for m in markets:
        liq = _market_liq(m)
        if liq > best_liq:
            best, best_liq = m, liq
    return best


def lp_lock_status(report: dict) -> dict:
    """Status kunci LP yang sadar tipe pool.

    Return:
      locked_pct        -- rata-rata tertimbang likuiditas, HANYA dari pool yang
                           LP-nya memang bisa dikunci. None kalau tak ada.
      lockable_share    -- porsi likuiditas (0..1) yang ada di pool lockable.
                           Kalau kecil, angka locked_pct tidak mewakili apa pun.
      measurable        -- True kalau LP lock layak dijadikan dasar keputusan.
      concentrated_share-- porsi likuiditas di pool CLMM/DLMM.
    """
    markets = report.get("markets") or []
    total_liq = sum(_market_liq(m) for m in markets)

    lockable_liq = 0.0
    weighted = 0.0
    concentrated_liq = 0.0

    for m in markets:
        mtype = m.get("marketType") or ""
        liq = _market_liq(m)
        if mtype in CONCENTRATED_TYPES:
            concentrated_liq += liq
            continue
        if mtype not in LOCKABLE_LP_TYPES:
            continue
        lp = m.get("lp") or {}
        pct = lp.get("lpLockedPct")
        if pct is None:
            continue
        try:
            pct = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            continue
        # Pool tanpa likuiditas tetap dihitung dengan bobot minimum supaya
        # token yang seluruh LP-nya kosong tidak lolos begitu saja.
        w = liq if liq > 0 else 1.0
        lockable_liq += liq
        weighted += pct * w

    denom = sum(
        (_market_liq(m) if _market_liq(m) > 0 else 1.0)
        for m in markets
        if (m.get("marketType") or "") in LOCKABLE_LP_TYPES
        and (m.get("lp") or {}).get("lpLockedPct") is not None
    )

    locked_pct = (weighted / denom) if denom > 0 else None
    lockable_share = (lockable_liq / total_liq) if total_liq > 0 else 0.0
    concentrated_share = (concentrated_liq / total_liq) if total_liq > 0 else 0.0

    return {
        "locked_pct": locked_pct,
        "lockable_share": lockable_share,
        "concentrated_share": concentrated_share,
        # Hanya layak jadi dasar keputusan kalau mayoritas likuiditas memang
        # berada di pool yang LP-nya bisa dikunci.
        "measurable": locked_pct is not None and lockable_share >= 0.5,
    }


def transfer_fee_pct(report: dict) -> float:
    tf = report.get("transferFee") or {}
    try:
        return float(tf.get("pct") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def known_account_label(report: dict, address: str) -> tuple[str | None, str | None]:
    """Return (name, type) kalau alamat dikenal (LP vault, CEX, dll)."""
    ka = report.get("knownAccounts") or {}
    entry = ka.get(address)
    if not isinstance(entry, dict):
        return None, None
    return entry.get("name"), entry.get("type")


def risk_items(report: dict) -> list[dict]:
    out = []
    for r in report.get("risks") or []:
        if isinstance(r, dict) and r.get("name"):
            out.append(
                {
                    "name": r.get("name", ""),
                    "description": r.get("description", ""),
                    "level": (r.get("level") or "info").lower(),
                    "score": r.get("score") or 0,
                }
            )
    return out

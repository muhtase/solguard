"""Analisa cluster wallet — perkiraan risiko dump berjamaah (bundle).

Sumber: `insiderNetworks` dari RugCheck, yaitu kelompok wallet yang saling
terhubung lewat riwayat transfer token.

PENTING soal cara membacanya. Angka mentah "% dipegang cluster" TIDAK bisa
langsung disebut "bundled". Contoh nyata:

  Token pump.fun umur 22 menit : cluster 4 wallet  pegang  3,8%  -> 0,95%/wallet
  BONK (umur 3 tahun)          : cluster 8.447 wlt pegang 46,7%  -> 0,006%/wallet

Keduanya "cluster", tapi yang pertama adalah bundle sniper sungguhan,
sedangkan yang kedua cuma artefak: makin tua sebuah token, makin banyak wallet
yang pernah saling transfer, sampai grafnya menyatu jadi satu komponen raksasa.
Melaporkan BONK "46% bundled" itu alarm palsu.

Pembedanya = KEPADATAN, bukan total: berapa rata-rata suplai yang dipegang
tiap wallet di dalam cluster. Bundle sniper itu sedikit wallet dengan porsi
besar masing-masing.
"""
from __future__ import annotations

import config

from .models import ClusterInfo, ClusterReport


def _supply(report: dict) -> float | None:
    tok = report.get("token") or {}
    raw = tok.get("supply")
    dec = tok.get("decimals")
    if raw is None or dec is None:
        return None
    try:
        s = int(raw) / (10 ** int(dec))
        return s if s > 0 else None
    except (TypeError, ValueError):
        return None


def analyze(report: dict, age_hours: float | None = None) -> ClusterReport:
    out = ClusterReport()
    supply = _supply(report)
    if not supply:
        return out
    out.supply = supply

    tok_dec = int((report.get("token") or {}).get("decimals") or 0)

    networks = report.get("insiderNetworks") or []
    for n in networks:
        if not isinstance(n, dict):
            continue
        try:
            size = int(n.get("size") or 0)
            raw_amt = float(n.get("tokenAmount") or 0)
        except (TypeError, ValueError):
            continue
        if size <= 0 or raw_amt <= 0:
            continue

        held = raw_amt / (10**tok_dec)
        pct = held / supply * 100
        per_wallet = pct / size

        # Cluster rapat = sedikit wallet, porsi per wallet besar.
        bundle_like = (
            size <= config.BUNDLE_MAX_WALLETS
            and per_wallet >= config.BUNDLE_MIN_PCT_PER_WALLET
            and pct >= config.BUNDLE_MIN_TOTAL_PCT
        )

        out.clusters.append(
            ClusterInfo(
                id=str(n.get("id") or "?"),
                size=size,
                active=int(n.get("activeAccounts") or 0),
                pct_supply=round(pct, 3),
                per_wallet_pct=round(per_wallet, 4),
                link_type=str(n.get("type") or "?"),
                bundle_like=bundle_like,
            )
        )

    out.clusters.sort(key=lambda c: c.pct_supply, reverse=True)
    out.total_pct = round(sum(c.pct_supply for c in out.clusters), 2)
    out.bundle_pct = round(
        sum(c.pct_supply for c in out.clusters if c.bundle_like), 2
    )
    out.bundle_wallets = sum(c.size for c in out.clusters if c.bundle_like)
    out.cluster_count = len(out.clusters)

    # Saldo creator dihitung terpisah — dev dump itu risiko tersendiri
    try:
        cb = float(report.get("creatorBalance") or 0)
        if cb > 0:
            out.creator_pct = round((cb / (10**tok_dec)) / supply * 100, 3)
    except (TypeError, ValueError):
        pass

    # Pada token muda, cluster transfer jauh lebih mungkin bundle sungguhan
    # ketimbang graf organik yang menyatu seiring waktu.
    out.young_token = age_hours is not None and age_hours < 24 * 7

    return out

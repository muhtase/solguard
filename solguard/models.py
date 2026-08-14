"""Struktur data hasil agregasi antar-provider."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HolderActivity:
    """Satu baris di tabel top holder."""

    rank: int
    owner: str
    token_account: str
    pct: float
    ui_amount: float
    usd_value: float | None = None
    insider: bool = False
    label: str | None = None          # nama akun dikenal (LP vault, CEX, dsb)
    label_type: str | None = None     # tipe akun dikenal
    last_active_ts: int | None = None  # unix, transaksi terakhir di token account
    tx_24h: int = 0
    tx_total_seen: int = 0
    rpc_ok: bool = True

    # Arah aliran token (diisi flow.py)
    signatures: list[dict] = field(default_factory=list)
    flow_in: int = 0             # jumlah tx token masuk (beli/terima)
    flow_out: int = 0            # jumlah tx token keluar (jual/kirim)
    flow_in_amount: float = 0.0
    flow_out_amount: float = 0.0
    flow_requested: int = 0      # berapa tx yang ingin diperiksa
    flow_sampled: int = 0        # berapa tx yang benar-benar terbaca
    flow_moved: int = 0          # berapa tx yang mengubah saldo
    flow_ok: bool = False

    @property
    def is_infrastructure(self) -> bool:
        """LP pool / CEX / program — bukan wallet retail, jangan dihitung konsentrasi."""
        t = (self.label_type or "").lower()
        return any(k in t for k in ("amm", "liquidity", "market", "exchange", "cex", "program", "vault"))

    @property
    def is_locked(self) -> bool:
        """Kontrak vesting/locker. Tidak bisa dump sekarang, tapi akan unlock —
        tetap dihitung sebagai suplai yang bisa jatuh ke pasar nanti."""
        t = (self.label_type or "").lower()
        return "locker" in t or "vesting" in t


@dataclass
class ClusterInfo:
    """Satu kelompok wallet yang saling terhubung lewat transfer."""

    id: str
    size: int
    active: int
    pct_supply: float
    per_wallet_pct: float    # kepadatan — pembeda bundle vs graf organik
    link_type: str
    bundle_like: bool


@dataclass
class ClusterReport:
    clusters: list[ClusterInfo] = field(default_factory=list)
    cluster_count: int = 0
    total_pct: float = 0.0       # semua cluster digabung
    bundle_pct: float = 0.0      # hanya cluster yang rapat/mencurigakan
    bundle_wallets: int = 0
    creator_pct: float = 0.0
    supply: float = 0.0
    young_token: bool = False

    @property
    def worst(self) -> ClusterInfo | None:
        for c in self.clusters:
            if c.bundle_like:
                return c
        return self.clusters[0] if self.clusters else None


@dataclass
class TokenReport:
    mint: str

    # identitas
    name: str = "?"
    symbol: str = "?"
    creator: str | None = None
    launchpad: str | None = None
    verified: bool = False
    socials: dict[str, str] = field(default_factory=dict)

    # market
    price_usd: float | None = None
    market_cap: float | None = None
    fdv: float | None = None
    liquidity_usd: float = 0.0
    pool_count: int = 0
    main_dex: str | None = None
    pair_created_at: int | None = None   # unix ms
    age_hours: float | None = None

    # aktivitas
    volume_24h: float = 0.0
    buys_24h: int = 0
    sells_24h: int = 0
    price_change_24h: float | None = None
    price_change_1h: float | None = None
    traders_24h: int | None = None
    net_buyers_24h: int | None = None
    organic_score: float | None = None
    organic_label: str | None = None
    organic_ratio_24h: float | None = None
    holder_change_24h: float | None = None
    # Pertumbuhan holder per jendela waktu (persen).
    # 5m/1j/6j/24j dari Jupiter; 4j dihitung dari riwayat lokal karena Jupiter
    # tidak menyediakan jendela itu.
    holder_change_5m: float | None = None
    holder_change_1h: float | None = None
    holder_change_6h: float | None = None
    holder_change_4h: float | None = None
    holder_4h_baseline_age: int | None = None  # umur snapshot pembanding, detik
    holder_history_points: int = 0

    # keamanan
    mint_authority: str | None = None
    freeze_authority: str | None = None
    mutable_metadata: bool = False
    transfer_fee_pct: float = 0.0
    lp_locked_pct: float | None = None
    lp_lock_measurable: bool = False   # LP lock layak jadi dasar keputusan?
    lp_lockable_share: float = 0.0     # porsi likuiditas di pool ber-LP-token
    lp_concentrated_share: float = 0.0  # porsi likuiditas di pool CLMM/DLMM
    rugged: bool = False
    rugcheck_risk_score: int | None = None   # makin tinggi makin bahaya
    risks: list[dict] = field(default_factory=list)
    # True hanya kalau status mint/freeze authority benar-benar terbaca dari
    # sumber. Tanpa ini, nilai default None tidak bisa dibedakan dari
    # "authority memang sudah dimatikan" — dan itu bikin klaim aman palsu.
    authority_data_ok: bool = False
    metadata_data_ok: bool = False

    # distribusi
    holder_count: int | None = None
    top10_pct: float = 0.0
    top10_pct_ex_infra: float = 0.0
    dev_balance_pct: float | None = None
    dev_mints: int | None = None
    insider_count: int = 0
    graph_insiders: int = 0
    holders: list[HolderActivity] = field(default_factory=list)
    clusters: ClusterReport = field(default_factory=ClusterReport)

    # meta eksekusi
    sources_ok: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Verdict:
    score: float                       # 0..100, makin tinggi makin layak
    label: str                         # teks putusan
    emoji: str
    pillars: dict[str, float] = field(default_factory=dict)
    hard_fails: list[str] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    suggested_size: str = ""

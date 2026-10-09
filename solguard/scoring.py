"""Mesin penilaian: ubah TokenReport jadi Verdict yang bisa ditindaklanjuti.

Filosofi:
  1. HARD FAIL dulu. Ada hal yang bikin token tidak layak beli berapa pun
     bagusnya metrik lain (mint authority hidup, LP tidak terkunci, rugged).
     Ini tidak bisa ditutupi oleh skor tinggi di pilar lain.
  2. Sisanya skor 4 pilar berbobot: keamanan, distribusi, likuiditas, aktivitas.
  3. Skor BUKAN prediksi harga. Ini ukuran "seberapa besar peluang lo dirampok
     atau kejebak likuiditas", bukan "seberapa besar peluang naik".
  4. (Okt 2026, dari framework mage) Skor RISIKO dan sinyal PELUANG dipisah
     total. Dulu pilar "aktivitas" mencampur tekanan beli, holder naik, dan top
     holder akumulasi ke dalam angka yang sama dengan mint authority — satu
     angka gabungan jadi kotak hitam. Sekarang pilar aktivitas HANYA menilai
     kualitas/manipulasi (wash trading, volume palsu, sepi). Semua sinyal arah
     pindah ke lapisan perilaku (divergence.py) dan panel terpisah.
"""
from __future__ import annotations

import config

from .models import TokenReport, Verdict

WEIGHTS = {
    "security": 0.35,
    "distribution": 0.25,
    "liquidity": 0.20,
    "activity": 0.20,
}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# Hard fails
# --------------------------------------------------------------------------- #
def _hard_fails(rep: TokenReport) -> list[str]:
    fails: list[str] = []

    if rep.mint_authority:
        fails.append("Mint authority MASIH AKTIF — dev bisa cetak suplai baru kapan saja")
    if rep.freeze_authority:
        fails.append("Freeze authority MASIH AKTIF — dev bisa bekukan token lo, gak bisa jual")
    if rep.rugged:
        fails.append("RugCheck sudah menandai token ini RUGGED")
    if rep.transfer_fee_pct >= 10:
        fails.append(f"Transfer fee {rep.transfer_fee_pct:.1f}% — tiap transfer dipotong besar")
    if rep.liquidity_usd < config.MIN_LIQUIDITY_USD:
        fails.append(
            f"Likuiditas cuma ${rep.liquidity_usd:,.0f} "
            f"(min ${config.MIN_LIQUIDITY_USD:,.0f}) — keluar posisi bakal ancur slippage"
        )
    # Hanya hard-fail kalau LP lock memang TERUKUR, yaitu mayoritas likuiditas
    # ada di pool ber-LP-token. Untuk pool CLMM/DLMM angka ini tidak berlaku
    # dan RugCheck mengisinya 0 — menganggapnya "tidak terkunci" salah besar.
    if (
        rep.lp_lock_measurable
        and rep.lp_locked_pct is not None
        and rep.lp_locked_pct < 50
        and rep.liquidity_usd > 0
    ):
        fails.append(
            f"LP cuma terkunci {rep.lp_locked_pct:.0f}% — sisanya bisa ditarik dev kapan saja"
        )

    # Cluster rapat yang menguasai sebagian besar suplai = rug menunggu waktu
    if rep.clusters.bundle_pct >= 40:
        w = rep.clusters.worst
        detail = f" ({w.size} wallet)" if w else ""
        fails.append(
            f"Cluster terkoordinasi menguasai {rep.clusters.bundle_pct:.0f}% suplai{detail} "
            "— satu perintah dump bisa menghabisi likuiditas"
        )

    for r in rep.risks:
        nm = r["name"].lower()
        if r["level"] == "danger" and ("rugged" in nm and "creator" in nm):
            fails.append("Creator punya riwayat merilis token yang sudah rug")
    return fails


# --------------------------------------------------------------------------- #
# Pilar
# --------------------------------------------------------------------------- #
def _security_score(rep: TokenReport, notes: dict) -> float:
    s = 100.0

    # Tanpa data authority yang terbaca, kita TIDAK boleh menyimpulkan aman.
    # Nilai None default identik dengan "authority sudah mati", jadi klaim
    # positif di sini harus dikunci ke ketersediaan sumber.
    if not rep.authority_data_ok:
        s -= 35
        notes["neg"].append("Status mint/freeze authority tidak terbaca — anggap belum aman")
    else:
        if rep.mint_authority:
            s -= 100
        if rep.freeze_authority:
            s -= 100
        if not rep.mint_authority and not rep.freeze_authority:
            notes["pos"].append("Mint & freeze authority sudah dimatikan")

    if rep.metadata_data_ok:
        if rep.mutable_metadata:
            s -= 10
            notes["neg"].append("Metadata masih mutable (nama/logo bisa diubah dev)")
        else:
            notes["pos"].append("Metadata immutable")

    if rep.transfer_fee_pct > 0:
        s -= min(45.0, rep.transfer_fee_pct * 5)
        notes["neg"].append(f"Ada transfer fee {rep.transfer_fee_pct:.2f}%")

    if rep.lp_lock_measurable and rep.lp_locked_pct is not None:
        if rep.lp_locked_pct >= 99:
            notes["pos"].append("LP terkunci/burn 100%")
        else:
            s -= (100 - rep.lp_locked_pct) * 0.45
            notes["neg"].append(f"LP terkunci {rep.lp_locked_pct:.0f}% (tertimbang likuiditas)")
    elif rep.lp_concentrated_share >= 0.5:
        # Likuiditas mayoritas di CLMM/DLMM: LP lock tidak berlaku. Ini bukan
        # sinyal buruk, tapi juga bukan jaminan — beri penalti ketidakpastian kecil.
        s -= 5
        notes["neg"].append(
            f"{rep.lp_concentrated_share * 100:.0f}% likuiditas di pool CLMM/DLMM — "
            "konsep 'LP terkunci' tidak berlaku, cek manual siapa pemilik posisinya"
        )
    else:
        s -= 12
        notes["neg"].append("Status kunci LP tidak terbaca")

    danger = sum(1 for r in rep.risks if r["level"] == "danger")
    warn = sum(1 for r in rep.risks if r["level"] == "warn")
    s -= danger * 22 + warn * 6

    if rep.dev_mints and rep.dev_mints > 50:
        s -= 12
        notes["neg"].append(f"Dev sudah pernah bikin {rep.dev_mints} token lain (serial deployer)")

    if rep.verified:
        s += 5
        notes["pos"].append("Terverifikasi di Jupiter")

    return _clamp(s)


def _distribution_score(rep: TokenReport, notes: dict) -> float:
    s = 100.0

    # Konsentrasi tanpa pool LP / CEX — ini yang benar-benar bisa dump
    conc = rep.top10_pct_ex_infra or rep.top10_pct
    if conc:
        if conc <= 15:
            notes["pos"].append(f"Top 10 wallet cuma pegang {conc:.1f}% — tersebar rata")
        elif conc >= 40:
            notes["neg"].append(f"Top 10 wallet pegang {conc:.1f}% — risiko dump tinggi")
        s -= max(0.0, (conc - 15)) * 1.8

    if rep.insider_count:
        s -= rep.insider_count * 9
        notes["neg"].append(f"{rep.insider_count} dari top 10 ditandai insider oleh RugCheck")

    # Cluster rapat = risiko dump berjamaah dalam satu gerakan
    cl = rep.clusters
    if cl.bundle_pct > 0:
        w = cl.worst
        # Token muda memperkuat sinyal: graf transfer belum sempat menyatu
        # secara organik, jadi cluster rapat hampir pasti koordinasi.
        mult = 2.6 if cl.young_token else 1.8
        s -= min(45.0, cl.bundle_pct * mult)
        detail = f"{cl.bundle_wallets} wallet"
        if w:
            detail = f"{w.size} wallet, rata-rata {w.per_wallet_pct:.2f}%/wallet"
        notes["neg"].append(
            f"Cluster terkoordinasi pegang {cl.bundle_pct:.1f}% suplai ({detail}) "
            "— bisa dump barengan"
        )
    elif cl.cluster_count and cl.total_pct >= 10:
        # Ada cluster besar tapi menyebar — catat tanpa menghukum berat
        notes["neg"].append(
            f"Ada jaringan transfer luas ({cl.total_pct:.0f}% suplai) tapi menyebar tipis "
            "— kemungkinan graf organik, bukan bundle"
        )
        s -= 4

    # Jumlah insider harus dibaca relatif terhadap total holder. 8.000 insider
    # dari 2 juta holder (0,4%) beda jauh artinya dari 80 insider dari 500 holder.
    if rep.graph_insiders:
        hc_total = rep.holder_count or 0
        if hc_total > 0:
            ins_ratio = rep.graph_insiders / hc_total * 100
            if ins_ratio >= 5:
                s -= min(25.0, ins_ratio)
                notes["neg"].append(
                    f"{rep.graph_insiders:,} wallet insider "
                    f"({ins_ratio:.1f}% dari seluruh holder)"
                )
            elif ins_ratio >= 1:
                s -= 6
                notes["neg"].append(
                    f"{rep.graph_insiders:,} wallet insider ({ins_ratio:.1f}% dari holder)"
                )
        else:
            s -= 10
            notes["neg"].append(f"{rep.graph_insiders:,} wallet terdeteksi jaringan insider")

    if rep.dev_balance_pct is not None:
        if rep.dev_balance_pct > 10:
            s -= 30
            notes["neg"].append(f"Dev masih pegang {rep.dev_balance_pct:.1f}% suplai")
        elif rep.dev_balance_pct > 3:
            s -= 12
            notes["neg"].append(f"Dev pegang {rep.dev_balance_pct:.1f}% suplai")
        elif rep.dev_balance_pct < 0.5:
            notes["pos"].append("Dev nyaris tidak pegang suplai")

    hc = rep.holder_count or 0
    if hc < 100:
        s -= 40
        notes["neg"].append(f"Holder baru {hc} — terlalu sepi")
    elif hc < 500:
        s -= 20
        notes["neg"].append(f"Holder masih {hc}")
    elif hc < 2000:
        s -= 8
    else:
        notes["pos"].append(f"{hc:,} holder")

    return _clamp(s)


def _liquidity_score(rep: TokenReport, notes: dict) -> float:
    liq = rep.liquidity_usd
    if liq < 5_000:
        s = 12.0
    elif liq < 20_000:
        s = 38.0
    elif liq < 50_000:
        s = 58.0
    elif liq < 150_000:
        s = 74.0
    elif liq < 500_000:
        s = 87.0
    else:
        s = 95.0
        notes["pos"].append(f"Likuiditas tebal ${liq:,.0f}")

    # Rasio likuiditas terhadap market cap — mcap gede tapi LP tipis = jebakan
    mc = rep.market_cap or rep.fdv or 0
    if mc > 0 and liq > 0:
        ratio = liq / mc * 100
        if ratio < 1:
            s -= 28
            notes["neg"].append(f"LP cuma {ratio:.2f}% dari market cap — exit bakal berat")
        elif ratio < 3:
            s -= 12
            notes["neg"].append(f"LP tipis, {ratio:.1f}% dari market cap")
        elif ratio > 15:
            s += 5
            notes["pos"].append(f"LP sehat, {ratio:.1f}% dari market cap")

    if rep.pool_count <= 1:
        s -= 10
        notes["neg"].append("Cuma ada 1 pool — gampang dimanipulasi")
    elif rep.pool_count >= 5:
        notes["pos"].append(f"Likuiditas tersebar di {rep.pool_count} pool")

    if rep.age_hours is not None:
        if rep.age_hours < 1:
            s -= 25
            notes["neg"].append("Token baru lahir < 1 jam — belum teruji sama sekali")
        elif rep.age_hours < 24:
            s -= 12
            notes["neg"].append(f"Token baru {rep.age_hours:.0f} jam")
        elif rep.age_hours > 24 * 30:
            notes["pos"].append(f"Sudah bertahan {rep.age_hours / 24:.0f} hari")

    return _clamp(s)


def _activity_score(rep: TokenReport, notes: dict) -> float:
    s = 60.0

    # Organic score Jupiter = volume setelah difilter dari bot/wash trading
    if rep.organic_score is not None:
        s = _clamp(float(rep.organic_score))
        lbl = (rep.organic_label or "").lower()
        if lbl == "high":
            notes["pos"].append(f"Aktivitas organik tinggi (organic score {rep.organic_score:.0f})")
        elif lbl == "low":
            notes["neg"].append(f"Aktivitas organik rendah (organic score {rep.organic_score:.0f})")

    if rep.organic_ratio_24h is not None and rep.volume_24h > 0:
        pctorg = rep.organic_ratio_24h * 100
        if pctorg < 5:
            s -= 20
            notes["neg"].append(f"Cuma {pctorg:.1f}% volume 24j yang organik — sisanya bot/wash")
        elif pctorg > 30:
            notes["pos"].append(f"{pctorg:.0f}% volume 24j organik")

    # Volume jauh melampaui likuiditas = indikasi wash trading
    if rep.liquidity_usd > 0 and rep.volume_24h > 0:
        turnover = rep.volume_24h / rep.liquidity_usd
        if turnover > 50:
            s -= 22
            notes["neg"].append(f"Volume {turnover:.0f}x likuiditas — pola wash trading")
        elif turnover < 0.1:
            s -= 15
            notes["neg"].append("Volume nyaris mati dibanding likuiditas")

    # Arah (buy vs sell ratio) bukan urusan skor risiko -> divergence.py.
    # Yang tersisa di sini cuma "ada kehidupan atau tidak".
    if rep.buys_24h + rep.sells_24h == 0:
        s -= 25
        notes["neg"].append("Tidak ada transaksi 24 jam terakhir")

    if rep.traders_24h is not None:
        if rep.traders_24h < 50:
            s -= 15
            notes["neg"].append(f"Cuma {rep.traders_24h} trader unik dalam 24j")
        elif rep.traders_24h > 1000:
            notes["pos"].append(f"{rep.traders_24h:,} trader unik dalam 24j")

    # Arah gerak top holder & pertumbuhan holder: sinyal PELUANG, bukan risiko.
    # Dipindah ke divergence.directional_notes() supaya tidak mencampur skor.

    return _clamp(s)


# --------------------------------------------------------------------------- #
def evaluate(rep: TokenReport) -> Verdict:
    notes: dict[str, list[str]] = {"pos": [], "neg": []}

    pillars = {
        "security": _security_score(rep, notes),
        "distribution": _distribution_score(rep, notes),
        "liquidity": _liquidity_score(rep, notes),
        "activity": _activity_score(rep, notes),
    }
    score = sum(pillars[k] * WEIGHTS[k] for k in pillars)

    fails = _hard_fails(rep)

    # Data tidak lengkap = ketidakpastian, bukan kabar baik. Potong skornya.
    missing = [k for k, ok in rep.sources_ok.items() if not ok]
    if missing:
        score *= 1 - 0.12 * len(missing)
        notes["neg"].append(f"Sumber data tidak lengkap: {', '.join(missing)}")

    if fails:
        score = min(score, 24.0)

    score = round(_clamp(score), 1)

    # Label dibaca sebagai RISIKO. "Aman" di sini bukan "bakal naik" —
    # peluang dibaca di panel perilaku yang terpisah.
    if fails:
        emoji, label = "🔴", "CACAT FATAL"
        size = "Skip. Ada cacat yang gak sebanding sama upside apa pun."
    elif score >= 78:
        emoji, label = "🟢", "RISIKO RENDAH"
        size = "Struktur token relatif bersih. Peluangnya? Lihat panel perilaku."
    elif score >= 62:
        emoji, label = "🟢", "RISIKO TERKENDALI"
        size = "Ada catatan yang harus lo terima. Size wajar, stop wajib."
    elif score >= 46:
        emoji, label = "🟡", "BERISIKO"
        size = "Kalau tetap mau, anggap uang hangus. Size mikro maksimal."
    elif score >= 32:
        emoji, label = "🟠", "RISIKO TINGGI"
        size = "Lebih banyak alasan buat skip daripada masuk."
    else:
        emoji, label = "🔴", "JANGAN SENTUH"
        size = "Skip."

    return Verdict(
        score=score,
        label=label,
        emoji=emoji,
        pillars={k: round(v, 1) for k, v in pillars.items()},
        hard_fails=fails,
        positives=notes["pos"],
        negatives=notes["neg"],
        suggested_size=size,
    )

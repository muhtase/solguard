# SolGuard

Bot Telegram pengecek keamanan & kelayakan token Solana. Feed contract address,
bot balas dengan audit keamanan, distribusi holder, aktivitas top 10 on-chain,
dan skor 0–100 dengan putusan layak beli atau tidak.

Semua sumber data **gratis** — tidak butuh API key berbayar.

---

## Cara jalanin

```bash
cd /root/solguard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# isi TELEGRAM_BOT_TOKEN dari @BotFather
nano .env

.venv/bin/python main.py
```

Uji pipeline tanpa Telegram:

```bash
.venv/bin/python test_cli.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

### Jalanin permanen pakai PM2

```bash
pm2 start /root/solguard/.venv/bin/python \
  --name solguard \
  --cwd /root/solguard \
  -- main.py

pm2 save
```

---

## Konfigurasi (`.env`)

| Variabel | Default | Keterangan |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | **Wajib.** Dari @BotFather |
| `ALLOWED_USER_IDS` | kosong | Batasi akses, user id dipisah koma. Kosong = publik |
| `SOLANA_RPC_URL` | public RPC | **Pakai Helius** — lihat tabel setelan RPC di bawah |
| `FLOW_TX_PER_HOLDER` | `6` | Transaksi per holder untuk nentuin arah beli/jual. `0` = matikan |
| `RPC_BATCH_SIZE` | `1` | `1` = request tunggal paralel. Naikkan hanya kalau RPC mengizinkan batch |
| `FLOW_BUDGET_SECONDS` | `12` | Anggaran waktu keras analisa arah; lewat itu sisanya dilaporkan `?` |
| `MIN_LIQUIDITY_USD` | `3000` | Di bawah ini token dianggap tidak layak trade |
| `CACHE_TTL` | `90` | Detik cache hasil analisa |

---

## Sumber data

| Sumber | Dipakai untuk |
|---|---|
| **RugCheck** | mint/freeze authority, LP lock per pool, top holder, insider, riwayat creator |
| **DexScreener** | harga, likuiditas per pool, txns, volume, sosial |
| **Jupiter Token API v2** | organic score (volume setelah difilter bot/wash), holder count, saldo dev |
| **Solana RPC** | riwayat transaksi token account tiap top holder |

Kalau salah satu sumber down, bot tetap jalan tapi skornya dipotong 12% per
sumber yang hilang — data tidak lengkap itu ketidakpastian, bukan kabar baik.

---

## Cara skor dihitung

**Langkah 1 — cacat fatal.** Ada hal yang bikin token tidak layak beli berapa
pun bagusnya metrik lain. Kalau salah satu kena, skor langsung dipaksa ≤24 dan
putusan jadi 🔴 JANGAN BELI:

- Mint authority masih aktif (dev bisa cetak suplai baru)
- Freeze authority masih aktif (dev bisa bekukan token lo)
- Sudah ditandai *rugged* oleh RugCheck
- Transfer fee ≥ 10%
- Likuiditas di bawah `MIN_LIQUIDITY_USD`
- LP terkunci < 50% **dan terukur** (lihat catatan LP lock di bawah)
- Creator punya riwayat merilis token yang sudah rug
- Cluster terkoordinasi menguasai ≥40% suplai

**Langkah 2 — empat pilar berbobot:**

| Pilar | Bobot | Isi |
|---|---|---|
| Keamanan | 35% | authority, LP lock, transfer fee, mutability, temuan RugCheck, serial deployer |
| Distribusi | 25% | konsentrasi top 10 (tanpa pool LP), **cluster/bundle**, insider, saldo dev, jumlah holder |
| Likuiditas | 20% | nominal LP, rasio LP/market cap, jumlah pool, umur token |
| Aktivitas | 20% | organic score, rasio volume organik, turnover, buy/sell, trader unik, **arah gerak top holder** |

**Band putusan:**

| Skor | Putusan |
|---|---|
| 78–100 | 🟢 Layak dipertimbangkan |
| 62–77 | 🟢 Boleh, tapi hati-hati |
| 46–61 | 🟡 Berisiko — size mikro |
| 32–45 | 🟠 Risiko tinggi |
| 0–31 | 🔴 Jangan beli |

> Skor ini mengukur **seberapa besar peluang lo dirampok atau kejebak
> likuiditas** — bukan prediksi harga naik. Token dengan skor 85 tetap bisa
> turun 90%, dan itu bukan kegagalan bot.

---

## Dua hal yang sering salah di tool sejenis

**1. LP lock di pool concentrated liquidity.**
`lpLockedPct` cuma berlaku untuk AMM klasik yang punya LP token fungible
(Raydium v4, Meteora, Pump.fun AMM). Di pool CLMM/DLMM (Orca Whirlpool,
Raydium CLMM, Meteora DLMM) posisi LP itu **NFT dengan range harga**, jadi
RugCheck mengisi `lpLockedPct = 0` karena *tidak berlaku* — bukan karena LP-nya
bebas ditarik.

Tool yang membaca angka itu mentah-mentah akan menandai token besar seperti
BONK sebagai rug (91% likuiditas BONK ada di pool CLMM/DLMM). SolGuard hanya
memakai LP lock sebagai dasar keputusan kalau **mayoritas likuiditas memang ada
di pool yang LP-nya bisa dikunci**, dan menghitungnya sebagai rata-rata
tertimbang likuiditas lintas pool. Selain itu statusnya ditulis `n/a` disertai
porsi likuiditas CLMM-nya.

**2. Ketiadaan data bukan bukti aman.**
Kalau status authority tidak terbaca, bot menulis `❔ tidak terbaca` dan
memotong skor — bukan menampilkan `✅ mati`. Field kosong yang dirender sebagai
centang hijau adalah cara paling gampang bikin orang percaya token scam.

---

## Kolom "Aktivitas Top 10 Holder"

Riwayat transaksi diambil dari **token account** tiap holder, bukan wallet
owner-nya. Artinya yang terhitung cuma pergerakan token yang sedang dicek —
bukan aktivitas wallet mereka secara umum.

| Kolom | Arti |
|---|---|
| `%` | porsi suplai yang dia pegang sekarang |
| `arah` | `BELI` token masuk · `JUAL` token keluar · `net+`/`net-` campur · `flat` tidak berubah |
| `net` | perubahan posisi dia dalam 7 hari (`new` = posisi baru dibuka) |
| `Terakhir` | kapan dia terakhir menyentuh token ini (`m` menit, `j` jam, `hr` hari) |

Flag: 🏦 pool/CEX · 🔒 vesting/locker · 🕵️ insider · 🐋 whale ≥5%

Baris `Top 10 (non-LP)` adalah yang penting: itu porsi suplai yang benar-benar
dipegang wallet dan bisa di-dump.

**Tiga keadaan yang sengaja dibedakan** di kolom `arah`, jangan tertukar:

- `—` tidak dianalisa — pool/CEX, atau memang tidak ada transaksi dalam 7 hari
- `?` **data gagal diambil dari RPC** — bukan berarti holder-nya diam
- `flat` data terbaca, saldonya memang tidak berubah

Cara `arah` dihitung: ambil beberapa transaksi terakhir yang menyentuh token
account holder, lalu bandingkan `preTokenBalances` vs `postTokenBalances` untuk
mint tsb. Selisih positif = masuk, negatif = keluar.

Pencocokan dilakukan lewat **(owner, mint)**, bukan `accountIndex`. Alasannya:
di transaksi versi 0, daftar akun lengkap = `accountKeys` **+**
`meta.loadedAddresses` (Address Lookup Table), dan swap lewat Jupiter hampir
selalu pakai LUT — mapping index gampang meleset satu dan menghasilkan label
beli/jual yang terbalik. Field `owner` dan `mint` ada langsung di entri saldo,
jadi kebal masalah itu.

> Jujurnya: ini mengukur token **masuk/keluar wallet**, bukan konfirmasi trade
> di DEX. Transfer antar-wallet sendiri ikut terhitung. Untuk pertanyaan
> "holder ini lagi nambah atau buang", itu justru yang relevan — mindahin ke
> wallet lain sebelum dump adalah pola yang sama bahayanya.

### Setelan RPC — ini penentu kualitas kolom `arah`

Dua RPC berperilaku sangat berbeda, dan setelan yang benar untuk satu justru
merusak yang lain. Hasil pengukuran langsung (24 Jul 2026):

| | Public RPC | Helius free |
|---|---|---|
| Batch JSON-RPC | maks **3** per request | **ditolak total** — "only available for paid plans" |
| Request tunggal | lambat, sering 429 | ~10 RPS, 100% sukses berurutan |
| Konkurensi optimal | rendah | **4** (36/40 sukses; 8+ ambruk kena 429) |
| Hasil analisa BONK | 8/23 tx, **50 detik** | semua tx, **3 detik** |

Karena itu `RPC_BATCH_SIZE=1` bukan berarti "lambat" — itu **mode request
tunggal paralel**, dan justru mode tercepat di Helius free karena batch-nya
ditolak. Paralelismenya diatur `RPC_CONCURRENCY`, bukan ukuran batch.

**Setelan Helius free (dipakai sekarang):**

```bash
SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=xxx
RPC_BATCH_SIZE=1        # Helius free menolak batch
RPC_BATCH_PACE=0.0
FLOW_TX_PER_HOLDER=6
```

**Setelan public RPC (fallback):**

```bash
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
RPC_BATCH_SIZE=3        # lebih dari 3 ditolak
RPC_BATCH_PACE=0.12
FLOW_TX_PER_HOLDER=4
```

Kalau punya Helius berbayar, batch jadi tersedia: `RPC_BATCH_SIZE=20`.

Satu jebakan yang sudah ditangani: di public RPC, penolakan sebagian datang
sebagai HTTP **200** berisi `{"error":{"code":429}}` per item. Kalau item itu
dibuang diam-diam, hasilnya terlihat seperti "holder tidak bergerak" padahal
datanya tidak pernah sampai — makanya item 429 dikumpulkan dan dicoba ulang,
dan yang tetap gagal dilaporkan `?`, bukan `—`.

---

## Tombol Refresh

Tiap laporan datang dengan tombol **🔄 Refresh** dan **📈 Chart**. Refresh
membuang cache untuk CA tersebut lalu menganalisa ulang dan menyunting pesan
yang sama di tempat — jadi tidak menumpuk pesan baru di chat.

Tanpa pembuangan cache, Refresh cuma akan menyajikan ulang data yang sama
selama `CACHE_TTL` dan tombolnya jadi tidak ada gunanya, jadi itu memang
dilakukan (`cache_drop(mint)` di `http.py`).

Footer laporan mencantumkan `Data per <waktu> WIB` supaya jelas kapan datanya
diambil. Waktu selalu dirender ke UTC+7 apa pun zona waktu servernya.

---

## Pertumbuhan holder

```
Pertumbuhan     : 1j +0.12% · 4j +0.45% · 6j +0.30% · 24j +2.10%
```

Jendela **1j, 6j, 24j** datang langsung dari Jupiter. Jendela **4 jam tidak
tersedia di sumber gratis mana pun** — Jupiter hanya punya 5m/1j/6j/24j, dan
RugCheck tidak punya deret waktu holder sama sekali.

Menginterpolasi 4 jam dari angka 6 jam akan menghasilkan angka yang terlihat
presisi padahal karangan. Jadi bot merekam sendiri jumlah holder tiap kali
sebuah token dicek (SQLite di `data/history.db`), lalu menghitung perubahan 4
jam dari snapshot asli begitu riwayatnya cukup umur.

Konsekuensinya: **saat pertama kali sebuah CA dicek, angka 4j belum ada**, dan
laporan mengatakannya terus terang berikut jumlah snapshot yang sudah terkumpul
— bukan menyembunyikan barisnya. Tombol Refresh mempercepat penumpukan riwayat.

Kalau snapshot pembanding tidak tepat 4 jam (toleransi ±40%), laporan menyebut
umur sebenarnya, misal *"angka 4j dibanding snapshot 3,2 jam lalu"*.

Snapshot lebih tua dari 3 hari dibuang otomatis.

---

## Panel "Cluster & Bundle"

Menjawab: **berapa persen suplai yang dipegang wallet terkoordinasi dan bisa
dump barengan.** Sumbernya `insiderNetworks` RugCheck — kelompok wallet yang
saling terhubung lewat riwayat transfer.

**Angka total tidak boleh dibaca mentah-mentah.** Contoh nyata dari dua token:

| Token | Cluster | % suplai | %/wallet | Putusan |
|---|---|---|---|---|
| pump.fun, umur 22 menit | 4 wallet | 3,8% | **0,95%** | ⚠️ BUNDLE |
| BONK, umur 3 tahun | 8.447 wallet | 46,7% | 0,006% | menyebar, bukan bundle |

Keduanya "cluster", tapi cuma yang pertama bundle sniper sungguhan. Makin tua
sebuah token, makin banyak wallet yang pernah saling transfer, sampai grafnya
menyatu jadi satu komponen raksasa. Melaporkan BONK "46% bundled" itu alarm
palsu yang bikin lo skip token normal.

Pembedanya **kepadatan**, bukan total: rata-rata suplai per wallet di dalam
cluster. Bundle = sedikit wallet, porsi besar masing-masing. Ambangnya diatur
lewat `BUNDLE_MAX_WALLETS`, `BUNDLE_MIN_PCT_PER_WALLET`, `BUNDLE_MIN_TOTAL_PCT`.

Panelnya menampilkan **jumlah cluster dan persentase totalnya**, lalu rincian
tiap cluster (jumlah wallet, % suplai, %/wallet, status bundle/menyebar):

```
🧬 CLUSTER & BUNDLE
  Total: 2 cluster pegang 46.99% suplai
  ✅ Tidak ada cluster rapat terdeteksi

#   wallet  % suplai  %/wallet  status
1     8453    46.67%    0.005%  menyebar
2       48     0.33%    0.007%  menyebar
```

Kalau clusternya lebih dari 8, sisanya diringkas jadi satu baris — tapi jumlah
dan persentase totalnya tetap disebut di baris atas, jadi tidak ada yang hilang
diam-diam.

Cluster rapat yang menguasai **≥40% suplai** dianggap cacat fatal — satu
perintah dump bisa menghabisi likuiditas.

> Ini deteksi cluster lewat graf transfer, **bukan** deteksi Jito bundle
> harfiah (wallet yang beli di blok yang sama saat launch). Untuk itu butuh
> data historis lengkap yang tidak tersedia di RPC gratis. Anggap ini proxy
> yang bagus, bukan pengganti persis.

---

## Struktur

```
main.py                    entrypoint
config.py                  konfigurasi dari .env
test_cli.py                uji pipeline tanpa Telegram
solguard/
  bot.py                   handler Telegram
  aggregate.py             gabung semua provider -> TokenReport
  scoring.py               mesin penilaian & verdict
  render.py                format pesan Telegram
  holders.py               analisa aktivitas top 10
  flow.py                  arah beli/jual tiap holder dari delta saldo
  clusters.py              deteksi bundle lewat kepadatan cluster
  history.py               snapshot holder (SQLite) untuk jendela 4 jam
  models.py                dataclass TokenReport / Verdict / ClusterReport
  http.py                  HTTP client + cache TTL + retry
  providers/
    rugcheck.py            keamanan, LP lock sadar-tipe-pool, top holder
    dexscreener.py         harga, likuiditas, volume
    jupiter.py             organic score, holder count, audit
    solrpc.py              riwayat on-chain + batch getTransaction
```

---

## Batasan yang perlu lo tahu

- **Tidak ada PnL top trader.** Butuh Birdeye/Helius berbayar. Yang ada sekarang
  arah gerak holder (nambah/buang) dan kapan terakhir gerak — bukan profit mereka.
- **Bundle detection bersifat proxy.** Berbasis graf transfer, bukan deteksi
  Jito bundle harfiah di blok launch.
- **Kolom arah butuh RPC yang layak.** Di public RPC sebagian holder akan
  muncul `?` karena `getTransaction` di-throttle. Dengan Helius free, tuntas.
- **Jendela analisa arah 7 hari.** Holder yang dorman lebih lama muncul `—`.
- **Tidak ada simulasi honeypot.** Di Solana honeypot lebih jarang daripada EVM,
  tapi token dengan transfer hook aneh tetap bisa lolos.
- **Label wallet terbatas** pada apa yang dikenali RugCheck. Wallet CEX yang
  tidak berlabel akan terhitung sebagai holder biasa, jadi angka "non-LP" bisa
  lebih pesimis dari kenyataan untuk token besar.
- **Rate limit.** Public RPC dan free tier RugCheck bisa throttle kalau bot
  dipakai ramai-ramai.

Bukan saran finansial. DYOR.

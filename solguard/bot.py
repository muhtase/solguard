"""Handler Telegram."""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config

from . import history
from .aggregate import analyze, behaviour
from .http import cache_drop, cache_purge, close_client, get_json
from .models import Behaviour
from .render import TELEGRAM_LIMIT, render_behaviour, render_calibration, render_report
from .scoring import evaluate

log = logging.getLogger(__name__)

# Alamat Solana: base58, 32-44 karakter (tanpa 0, O, I, l)
MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

REFRESH_PREFIX = "r:"

WELCOME = (
    "<b>SolGuard</b> — pengecek keamanan token Solana\n\n"
    "Kirim <b>contract address</b> token apa pun, gw balas dengan:\n"
    "  🔒 audit keamanan (mint/freeze auth, LP lock, transfer fee)\n"
    "  👥 distribusi holder + aktivitas top 10 on-chain\n"
    "  💰 likuiditas, volume, dan kualitas aktivitas (organik vs bot)\n"
    "  📊 skor RISIKO 0–100 (pesan 1)\n"
    "  🧠 pesan 2, terpisah: memori token (ATH/drawdown), smart money hadir/absen, "
    "urutan harga↔holder↔likuiditas, dan 'sejak lo pertama cek'\n\n"
    "Contoh:\n<code>DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263</code>\n\n"
    "Perintah: /check &lt;CA&gt; · /hasil · /id · /help\n\n"
    "<i>Skor risiko dan sinyal peluang sengaja DIPISAH. Tiap cek dicatat sebagai "
    "timestamp dan dipantau 24 jam — /hasil menunjukkan putusan bot ini benar atau tidak.</i>"
)

HELP = (
    "<b>Cara pakai</b>\n\n"
    "Tempel CA langsung, atau <code>/check &lt;CA&gt;</code>\n\n"
    "<b>Arti skor risiko</b> (pesan 1)\n"
    "🟢 78–100 — risiko rendah\n"
    "🟢 62–77 — risiko terkendali\n"
    "🟡 46–61 — berisiko, size mikro\n"
    "🟠 32–45 — risiko tinggi\n"
    "🔴 0–31 — jangan sentuh\n"
    "Skor ini bukan sinyal beli. Peluang dibaca di pesan 🧠.\n\n"
    "<b>Pesan 🧠 perilaku</b> (framework divergence @magersih)\n"
    "D1 memori: puncak, drawdown, apakah mantan runner, pola absorpsi\n"
    "D4 smart money: berapa wallet smart money (feed GMGN, DB lokal) sentuh token ini 7 hari, "
    "net beli/jual; NOL saat harga & holder lari = FOMO divergence\n"
    "D3 urutan: harga vs holder vs volume vs likuiditas 1j/6j/24j\n"
    "⏱ sejak cek pertama: return, MAE (drawdown terdalam), MFE\n\n"
    "<b>/hasil</b> — kalibrasi: tiap cek dipantau 24 jam, lalu median forward return & MAE "
    "per putusan dan per flag. Ini cara tahu fitur mana yang beneran punya nilai.\n\n"
    "<b>Cacat fatal</b> (langsung 🔴 apa pun skornya):\n"
    "mint/freeze authority masih aktif · LP terkunci &lt;50% · "
    "sudah ditandai rugged · transfer fee ≥10% · "
    f"likuiditas &lt;${config.MIN_LIQUIDITY_USD:,.0f} · creator punya riwayat rug\n\n"
    "<b>Bobot skor risiko</b>\n"
    "Keamanan 35% · Distribusi 25% · Likuiditas 20% · Kualitas tx 20%\n\n"
    "<b>Sumber data</b>\n"
    "RugCheck · DexScreener · Jupiter · Solana RPC · GMGN OpenAPI · DB smart money trench (lokal)"
)


def _authorized(update: Update) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in config.ALLOWED_USER_IDS)


async def _deny(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("Bot ini dibatasi. Akses ditolak.")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    await update.effective_message.reply_html(WELCOME, disable_web_page_preview=True)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    await update.effective_message.reply_html(HELP, disable_web_page_preview=True)


def _keyboard(mint: str, msg2_id: int | None = None) -> InlineKeyboardMarkup:
    """Tombol Refresh. callback_data dibatasi 64 byte: "r:" + mint (≤44) + ":" + id pesan 🧠."""
    data = f"{REFRESH_PREFIX}{mint}" + (f":{msg2_id}" if msg2_id else "")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=data),
                InlineKeyboardButton(
                    "📈 Chart", url=f"https://dexscreener.com/solana/{mint}"
                ),
            ]
        ]
    )


async def _build_report(mint: str, *, fresh: bool = False):
    """Return (teks laporan risiko, rep, verdict), atau None kalau semua sumber gagal."""
    cache_purge()
    if fresh:
        # Tanpa ini, Refresh cuma menyajikan ulang isi cache dan jadi percuma
        cache_drop(mint)
    rep = await analyze(mint)
    if not any(rep.sources_ok.values()):
        return None

    verdict = evaluate(rep)
    text = render_report(rep, verdict)
    if len(text) > TELEGRAM_LIMIT - 96:
        # Susun ulang lebih ringkas; memotong string mentah bisa membelah
        # tag HTML dan bikin Telegram menolak seluruh pesan.
        text = render_report(rep, verdict, compact=True)
        log.info("Laporan %s dipadatkan (%d char)", mint[:8], len(text))
    return text, rep, verdict


async def _build_behaviour(rep, verdict, *, user_id: int | None, journal: bool) -> str:
    """Pesan kedua. Juga mencatat cek ini ke jurnal (callout = timestamp)."""
    try:
        b = await behaviour(rep)
    except Exception:
        log.exception("Lapisan perilaku gagal untuk %s", rep.mint)
        b = Behaviour()
        b.smart.reading = "Lapisan perilaku error — lihat log."
    first = history.first_callout(rep.mint)
    path = history.path_since(rep.mint, first.ts) if first else None
    if journal:
        history.log_check(
            rep.mint, user_id=user_id, price=rep.price_usd, mcap=rep.market_cap,
            liq=rep.liquidity_usd, risk_score=verdict.score, label=verdict.label, flags=b.flags,
        )
    text = render_behaviour(rep, b, first, path)
    if len(text) > TELEGRAM_LIMIT - 96:
        text = render_behaviour(rep, b, first, path, compact=True)
    return text


async def _run_check(update: Update, mint: str) -> None:
    msg = update.effective_message
    user_id = update.effective_user.id if update.effective_user else None
    await msg.chat.send_action(ChatAction.TYPING)
    status = await msg.reply_html(f"🔎 Menganalisa <code>{mint[:8]}…</code>…")

    try:
        built = await _build_report(mint)
        if built is None:
            await status.edit_text(
                "❌ Gagal mengambil data untuk CA ini.\n"
                "Pastikan alamatnya benar dan itu token Solana, lalu coba lagi.",
            )
            return
        text, rep, verdict = built

        # Pesan 2 dikirim dulu sebagai placeholder supaya id-nya bisa ditaruh
        # di tombol Refresh pesan 1 — Refresh lalu memperbarui keduanya.
        status2: Message = await status.reply_html("🧠 Membaca perilaku &amp; smart money…")
        await status.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_keyboard(mint, status2.message_id),
        )
        # Pesan 1 sudah terkirim utuh; kegagalan pesan 2 tidak boleh menimpanya
        try:
            text2 = await _build_behaviour(rep, verdict, user_id=user_id, journal=True)
            await status2.edit_text(text2, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            log.exception("Pesan perilaku gagal untuk %s", mint)
            await status2.edit_text("⚠️ Lapisan perilaku gagal dimuat. Laporan risiko di atas tetap berlaku.")
    except Exception:
        log.exception("Analisa gagal untuk %s", mint)
        await status.edit_text(
            "❌ Terjadi error saat menganalisa. Coba lagi sebentar lagi."
        )


async def on_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if not _authorized(update):
        await query.answer("Akses ditolak.", show_alert=True)
        return

    payload = (query.data or "")[len(REFRESH_PREFIX) :]
    mint, _, msg2 = payload.partition(":")
    msg2_id = int(msg2) if msg2.isdigit() else None
    if not MINT_RE.fullmatch(mint):
        await query.answer("CA tidak dikenali.", show_alert=True)
        return

    await query.answer("Mengambil data terbaru…")
    try:
        built = await _build_report(mint, fresh=True)
        if built is None:
            await query.answer("Gagal mengambil data. Coba lagi.", show_alert=True)
            return
        text, rep, verdict = built
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=_keyboard(mint, msg2_id),
            )
        except BadRequest as exc:
            # Terjadi kalau isi laporan identik dengan sebelumnya. Bukan error nyata.
            if "not modified" not in str(exc).lower():
                raise
        if msg2_id and query.message:
            user_id = update.effective_user.id if update.effective_user else None
            # Refresh = cek ulang, dicatat juga; jadi deret timestamp-nya rapat
            text2 = await _build_behaviour(rep, verdict, user_id=user_id, journal=True)
            try:
                await ctx.bot.edit_message_text(
                    text2, chat_id=query.message.chat_id, message_id=msg2_id,
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                )
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    log.warning("Edit pesan perilaku gagal: %s", exc)
    except BadRequest as exc:
        log.warning("Refresh gagal untuk %s: %s", mint, exc)
        await query.answer("Gagal memperbarui.", show_alert=True)
    except Exception:
        log.exception("Refresh gagal untuk %s", mint)
        await query.answer("Terjadi error. Coba lagi.", show_alert=True)


async def cmd_hasil(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    await update.effective_message.reply_html(render_calibration(history.calibration()))


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan user id — buat mengisi ALLOWED_USER_IDS supaya kuota GMGN tidak dipakai orang asing."""
    u = update.effective_user
    await update.effective_message.reply_html(
        f"User id lo: <code>{u.id if u else '?'}</code>\n"
        f"Chat id: <code>{update.effective_chat.id}</code>\n\n"
        f"Taruh di <code>ALLOWED_USER_IDS</code> di .env lalu restart bot."
    )


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    args = ctx.args or []
    if not args:
        await update.effective_message.reply_html(
            "Format: <code>/check &lt;contract address&gt;</code>"
        )
        return
    m = MINT_RE.search(args[0])
    if not m:
        await update.effective_message.reply_text(
            "Itu bukan alamat Solana yang valid."
        )
        return
    await _run_check(update, m.group(0))


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    text = (update.effective_message.text or "").strip()
    m = MINT_RE.search(text)
    if not m:
        return  # abaikan chat biasa, jangan berisik di grup
    await _run_check(update, m.group(0))


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Error tak tertangani", exc_info=ctx.error)


DEX_BATCH = "https://api.dexscreener.com/tokens/v1/solana/"


async def _track_once() -> None:
    """Sampel harga/likuiditas token yang masih dalam jendela pantau 24 jam,
    lalu finalisasi callout yang sudah jatuh tempo. DexScreener batch 30/panggilan."""
    mints = history.pending_mints()
    samples: dict[str, tuple[float | None, float | None]] = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i : i + 30]
        data = await get_json(DEX_BATCH + ",".join(chunk))
        if not isinstance(data, list):
            continue
        best: dict[str, dict] = {}
        for p in data:
            if not isinstance(p, dict):
                continue
            m = (p.get("baseToken") or {}).get("address")
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            if m and (m not in best or liq > float((best[m].get("liquidity") or {}).get("usd") or 0)):
                best[m] = p
        for m, p in best.items():
            try:
                price = float(p.get("priceUsd")) if p.get("priceUsd") is not None else None
            except (TypeError, ValueError):
                price = None
            # likuiditas total semua pool, konsisten dengan analyze()
            liq_total = sum(
                float((q.get("liquidity") or {}).get("usd") or 0)
                for q in data if isinstance(q, dict) and (q.get("baseToken") or {}).get("address") == m
            )
            samples[m] = (price, liq_total or None)
    if samples:
        history.add_samples(samples)
    n = history.finalize_due()
    if mints or n:
        log.info("Pemantau: %d token disampel, %d callout difinalisasi", len(samples), n)


async def _tracker_loop() -> None:
    while True:
        try:
            await _track_once()
        except Exception:
            log.exception("Pemantau latar error")
        await asyncio.sleep(config.TRACK_INTERVAL)


async def _post_init(app: Application) -> None:
    app.bot_data["tracker"] = asyncio.create_task(_tracker_loop())


async def _post_shutdown(app: Application) -> None:
    task = app.bot_data.get("tracker")
    if task:
        task.cancel()
    await close_client()


def build_app() -> Application:
    if not config.BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN belum diisi. Salin .env.example jadi .env lalu isi tokennya."
        )

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("hasil", cmd_hasil))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CallbackQueryHandler(on_refresh, pattern=f"^{REFRESH_PREFIX}"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app

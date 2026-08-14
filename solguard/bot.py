"""Handler Telegram."""
from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

from .aggregate import analyze
from .http import cache_drop, cache_purge, close_client
from .render import TELEGRAM_LIMIT, render_report
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
    "  📊 skor 0–100 dengan putusan layak beli atau tidak\n\n"
    "Contoh:\n<code>DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263</code>\n\n"
    "Perintah: /check &lt;CA&gt; · /help\n\n"
    "<i>Skor ini mengukur risiko dirampok/kejebak likuiditas, "
    "BUKAN prediksi harga naik.</i>"
)

HELP = (
    "<b>Cara pakai</b>\n\n"
    "Tempel CA langsung, atau <code>/check &lt;CA&gt;</code>\n\n"
    "<b>Arti skor</b>\n"
    "🟢 78–100 — layak dipertimbangkan\n"
    "🟢 62–77 — boleh, tapi hati-hati\n"
    "🟡 46–61 — berisiko, size mikro\n"
    "🟠 32–45 — risiko tinggi\n"
    "🔴 0–31 — jangan beli\n\n"
    "<b>Cacat fatal</b> (langsung 🔴 apa pun skornya):\n"
    "mint/freeze authority masih aktif · LP terkunci &lt;50% · "
    "sudah ditandai rugged · transfer fee ≥10% · "
    f"likuiditas &lt;${config.MIN_LIQUIDITY_USD:,.0f} · creator punya riwayat rug\n\n"
    "<b>Bobot skor</b>\n"
    "Keamanan 35% · Distribusi 25% · Likuiditas 20% · Aktivitas 20%\n\n"
    "<b>Sumber data</b>\n"
    "RugCheck · DexScreener · Jupiter · Solana RPC"
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


def _keyboard(mint: str) -> InlineKeyboardMarkup:
    """Tombol Refresh. callback_data dibatasi 64 byte — "r:" + mint muat."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"{REFRESH_PREFIX}{mint}"),
                InlineKeyboardButton(
                    "📈 Chart", url=f"https://dexscreener.com/solana/{mint}"
                ),
            ]
        ]
    )


async def _build_report(mint: str, *, fresh: bool = False) -> str | None:
    """Return teks laporan, atau None kalau semua sumber gagal."""
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
    return text


async def _run_check(update: Update, mint: str) -> None:
    msg = update.effective_message
    await msg.chat.send_action(ChatAction.TYPING)
    status = await msg.reply_html(f"🔎 Menganalisa <code>{mint[:8]}…</code>…")

    try:
        text = await _build_report(mint)
        if text is None:
            await status.edit_text(
                "❌ Gagal mengambil data untuk CA ini.\n"
                "Pastikan alamatnya benar dan itu token Solana, lalu coba lagi.",
            )
            return

        await status.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_keyboard(mint),
        )
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

    mint = (query.data or "")[len(REFRESH_PREFIX) :]
    if not MINT_RE.fullmatch(mint):
        await query.answer("CA tidak dikenali.", show_alert=True)
        return

    await query.answer("Mengambil data terbaru…")
    try:
        text = await _build_report(mint, fresh=True)
        if text is None:
            await query.answer("Gagal mengambil data. Coba lagi.", show_alert=True)
            return
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_keyboard(mint),
        )
    except BadRequest as exc:
        # Terjadi kalau isi laporan identik dengan sebelumnya. Bukan error nyata,
        # tapi tetap perlu dikabari supaya tombolnya tidak terasa mati.
        if "not modified" in str(exc).lower():
            await query.answer("Belum ada perubahan data.")
        else:
            log.warning("Refresh gagal untuk %s: %s", mint, exc)
            await query.answer("Gagal memperbarui.", show_alert=True)
    except Exception:
        log.exception("Refresh gagal untuk %s", mint)
        await query.answer("Terjadi error. Coba lagi.", show_alert=True)


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


async def _post_shutdown(app: Application) -> None:
    await close_client()


def build_app() -> Application:
    if not config.BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN belum diisi. Salin .env.example jadi .env lalu isi tokennya."
        )

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CallbackQueryHandler(on_refresh, pattern=f"^{REFRESH_PREFIX}"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app

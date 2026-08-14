#!/usr/bin/env python3
"""SolGuard — bot Telegram pengecek keamanan & kelayakan token Solana."""
import logging

from solguard.bot import build_app

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    app = build_app()
    logging.info("SolGuard jalan. Tekan Ctrl+C untuk berhenti.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

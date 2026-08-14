#!/usr/bin/env python3
"""Uji pipeline analisa tanpa Telegram.

    python3 test_cli.py <CA> [<CA> ...]

Output-nya versi teks polos dari pesan yang akan dikirim bot.
"""
import asyncio
import logging
import re
import sys

from solguard.aggregate import analyze
from solguard.http import close_client
from solguard.render import render_report
from solguard.scoring import evaluate

logging.basicConfig(format="%(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

TAG = re.compile(r"<[^>]+>")


async def run(mints: list[str]) -> None:
    try:
        for mint in mints:
            print("=" * 66)
            rep = await analyze(mint)
            verdict = evaluate(rep)
            text = render_report(rep, verdict)
            print(TAG.sub("", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
            print()
            print(f"[debug] sumber: {rep.sources_ok}  pilar: {verdict.pillars}")
    finally:
        await close_client()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(run(sys.argv[1:]))

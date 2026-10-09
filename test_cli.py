#!/usr/bin/env python3
"""Uji pipeline analisa tanpa Telegram.

    python3 test_cli.py <CA> [<CA> ...]

Output-nya versi teks polos dari pesan yang akan dikirim bot.
"""
import asyncio
import logging
import re
import sys

from solguard import history
from solguard.aggregate import analyze, behaviour
from solguard.http import close_client
from solguard.render import render_behaviour, render_report
from solguard.scoring import evaluate

logging.basicConfig(format="%(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

TAG = re.compile(r"<[^>]+>")


def _plain(t: str) -> str:
    return TAG.sub("", t).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


async def run(mints: list[str]) -> None:
    try:
        for mint in mints:
            print("=" * 66)
            rep = await analyze(mint)
            verdict = evaluate(rep)
            text = render_report(rep, verdict)
            print(_plain(text))
            print(f"[debug] sumber: {rep.sources_ok}  pilar: {verdict.pillars}  ({len(text)} char)")
            print("-" * 66)
            b = await behaviour(rep, verdict)
            first = history.first_callout(mint)
            path = history.path_since(mint, first.ts) if first else None
            if "--journal" in sys.argv:
                history.log_check(mint, user_id=0, price=rep.price_usd, mcap=rep.market_cap,
                                  liq=rep.liquidity_usd, risk_score=verdict.score,
                                  label=verdict.label, flags=b.flags)
            text2 = render_behaviour(rep, b, first, path)
            print(_plain(text2))
            print(f"[debug] flags: {b.flags}  ({len(text2)} char)")
    finally:
        await close_client()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(run([a for a in sys.argv[1:] if not a.startswith("--")]))

"""Solana JSON-RPC — dipakai untuk membaca aktivitas on-chain top holder.

Trik penting: kita query signature pada TOKEN ACCOUNT (bukan wallet owner).
Riwayat transaksi token account = pergerakan token INI saja, jadi kita bisa tahu
kapan terakhir tiap top holder benar-benar menyentuh token yang sedang dicek —
bukan aktivitas wallet mereka secara umum.
"""
from __future__ import annotations

import asyncio
import logging

import config

from ..http import post_json

log = logging.getLogger(__name__)

_sem: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(config.RPC_CONCURRENCY)
    return _sem


async def get_signatures(address: str, limit: int = 40) -> list[dict]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit}],
    }
    async with _semaphore():
        data = await post_json(config.SOLANA_RPC_URL, payload)
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    return result if isinstance(result, list) else []


async def get_signatures_many(
    addresses: list[str], limit: int = 40
) -> dict[str, list[dict]]:
    tasks = [get_signatures(a, limit) for a in addresses]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, list[dict]] = {}
    for addr, res in zip(addresses, results):
        out[addr] = res if isinstance(res, list) else []
    return out


async def get_transactions(
    signatures: list[str],
    batch_size: int = config.RPC_BATCH_SIZE,
    max_rounds: int = 3,
    deadline: float | None = None,
) -> dict[str, dict]:
    """Ambil banyak transaksi lewat batch JSON-RPC.

    Public RPC membatasi `getTransaction` dengan ketat. Hasil pengukuran di
    api.mainnet-beta.solana.com: batch 3 lolos, batch 5 sebagian ditolak,
    batch 10 ditolak seluruhnya.

    Yang gampang terlewat: pada batch sebagian, RPC tetap membalas HTTP 200
    tapi menyelipkan `{"error": {"code": 429}}` di masing-masing item. Kalau
    item itu cuma dibuang diam-diam, hasilnya terlihat seperti "holder tidak
    bergerak" padahal datanya tidak pernah sampai. Karena itu item ber-429
    dikumpulkan lalu dicoba ulang, dan yang tetap gagal dilaporkan sebagai
    gagal — bukan sebagai nol.

    Return map signature -> transaksi. Signature yang gagal tidak muncul.
    """
    out: dict[str, dict] = {}
    if not signatures:
        return out

    opts = {"maxSupportedTransactionVersion": 0, "encoding": "json"}
    remaining = list(dict.fromkeys(signatures))  # buang duplikat, jaga urutan

    def out_of_time() -> bool:
        return deadline is not None and asyncio.get_event_loop().time() >= deadline

    async def fetch_single(sig: str) -> str | None:
        """Return signature kalau perlu dicoba ulang, None kalau selesai."""
        if out_of_time():
            return sig
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, opts],
        }
        async with _semaphore():
            data = await post_json(config.SOLANA_RPC_URL, payload, retries=1)
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            out[sig] = data["result"]
            return None
        return sig  # gagal / kena limit — coba lagi di putaran berikutnya

    async def fetch_batch(chunk: list[str]) -> list[str]:
        payload = [
            {
                "jsonrpc": "2.0",
                "id": j,
                "method": "getTransaction",
                "params": [sig, opts],
            }
            for j, sig in enumerate(chunk)
        ]
        async with _semaphore():
            # retries rendah: retry per-signature ditangani putaran di luar,
            # dan retry bertingkat bikin waktu meledak di RPC ter-throttle
            data = await post_json(config.SOLANA_RPC_URL, payload, retries=1)

        if not isinstance(data, list):
            return list(chunk)  # seluruh batch ditolak

        retry: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = item.get("id")
            if not isinstance(idx, int) or not 0 <= idx < len(chunk):
                continue
            sig = chunk[idx]
            result = item.get("result")
            if isinstance(result, dict):
                out[sig] = result
                continue
            err = item.get("error") or {}
            # 429 = layak dicoba lagi. Error lain (tx tidak ditemukan,
            # sudah dipangkas dari ledger) percuma diulang.
            if isinstance(err, dict) and err.get("code") in (429, -32005):
                retry.append(sig)
        return retry

    for rnd in range(max_rounds):
        if not remaining or out_of_time():
            break

        if batch_size <= 1:
            # Mode request tunggal — dipakai kalau RPC tidak mengizinkan batch
            # (Helius free tier menolak batch: "only available for paid plans").
            # Paralelismenya dijaga semaphore, bukan ukuran batch.
            results = await asyncio.gather(
                *(fetch_single(s) for s in remaining), return_exceptions=True
            )
            retry = [r for r in results if isinstance(r, str)]
        else:
            retry = []
            for i in range(0, len(remaining), batch_size):
                if out_of_time():
                    retry.extend(remaining[i:])
                    break
                retry.extend(await fetch_batch(remaining[i : i + batch_size]))
                await asyncio.sleep(config.RPC_BATCH_PACE)

        remaining = retry
        if remaining and not out_of_time():
            await asyncio.sleep(min(1.2 * (rnd + 1), 2.0))

    if remaining:
        log.info(
            "getTransaction: %d dari %d signature tetap gagal setelah %d putaran",
            len(remaining),
            len(set(signatures)),
            max_rounds,
        )
    return out

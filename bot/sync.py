"""Синхронизация журнала с Binance (через ключ Tiger.com) и снимок счёта для меню."""
import asyncio
import time
from collections import defaultdict

from .binance import Binance, BinanceError
from .crypto import decrypt
from .db import DB
from .prices import prices

EXCHANGE = "binance"
CLUSTER_GAP_MS = 60_000      # исполнения по одной паре с паузой < 60 с — одна сделка
SETTLE_MS = 60_000           # не трогаем последнюю минуту — сделка может ещё закрываться
FIRST_IMPORT_DAYS = 30
STABLE = {"USDT", "USDC", "BUSD", "FDUSD", "USD"}

_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_snap_cache: dict[int, tuple[float, dict | None]] = {}


def client(row) -> Binance:
    return Binance(row["api_key"], decrypt(row["secret_enc"]))


async def to_usd(amount: float, asset: str) -> float:
    if asset in STABLE:
        return amount
    price = (await prices.all_prices()).get(asset + "USDT")
    return amount * price if price else 0.0


def day_key(ms: int) -> tuple[str, int]:
    t = time.localtime(ms / 1000)
    start = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))
    return time.strftime("%Y%m%d", t), start


def cluster_pnl(rows: list[dict]) -> list[dict]:
    """Склеивает исполнения REALIZED_PNL в закрытые сделки."""
    open_: dict[str, dict] = {}
    trades: list[dict] = []
    for r in rows:
        if r["incomeType"] != "REALIZED_PNL":
            continue
        sym, ts = r["symbol"], int(r["time"])
        cur = open_.get(sym)
        if cur and ts - cur["last"] <= CLUSTER_GAP_MS and cur["asset"] == r["asset"]:
            cur["amount"] += float(r["income"])
            cur["last"] = ts
            cur["fills"] += 1
        else:
            if cur:
                trades.append(cur)
            open_[sym] = {"symbol": sym, "asset": r["asset"], "amount": float(r["income"]), "first": ts,
                          "last": ts, "id": f"{r['tranId']}", "fills": 1}
    trades += open_.values()
    return sorted(trades, key=lambda t: t["last"])


async def sync_user(db: DB, row, initial: bool = False) -> dict:
    """Импортирует новые доходы. Возвращает {'trades': [...], 'fees': x, 'funding': y}."""
    uid = row["user_id"]
    async with _locks[uid]:
        row = await db.get_api(uid, EXCHANGE) or row
        now = int(time.time() * 1000)
        start = row["last_sync_ms"] or day_key(now - FIRST_IMPORT_DAYS * 86400_000)[1] * 1000
        end = now - SETTLE_MS
        result = {"trades": [], "fees": 0.0, "funding": 0.0}
        if start >= end:
            return result
        income = await client(row).income(start, end)
        if not row["last_sync_ms"]:
            await db.drop_daily_imports(uid, ("bn:fee:", "bn:fund:"), start // 1000)

        for t in cluster_pnl(income):
            amount = await to_usd(t["amount"], t["asset"])
            note = "Binance Futures" + (f" · {t['fills']} исп." if t["fills"] > 1 else "")
            if await db.import_entry(uid, f"bn:pnl:{t['symbol']}:{t['id']}", "trade", amount,
                                     t["last"] // 1000, symbol=t["symbol"], note=note):
                result["trades"].append({**t, "usd": amount})

        for r in income:
            kind = r["incomeType"]
            if kind not in ("COMMISSION", "FUNDING_FEE"):
                continue
            amount = await to_usd(float(r["income"]), r["asset"])
            day, day_ts = day_key(int(r["time"]))
            if kind == "COMMISSION":
                result["fees"] += amount
                await db.accumulate_entry(uid, f"bn:fee:{day}", "expense", amount, day_ts,
                                          category="Комиссии", note="Binance Futures")
            else:
                result["funding"] += amount
                await db.accumulate_entry(uid, f"bn:fund:{day}", "expense", amount, day_ts,
                                          category="Фандинг", note="Binance Futures")
        await db.commit()
        await db.set_api_sync(uid, EXCHANGE, end + 1)
        _snap_cache.pop(uid, None)
        return result


async def snapshot(db: DB, uid: int, max_age: float = 20) -> dict | None:
    """Баланс фьючерсного счёта для шапки меню. None — не подключено или Binance недоступен."""
    cached = _snap_cache.get(uid)
    if cached and time.monotonic() - cached[0] < max_age:
        return cached[1]
    row = await db.get_api(uid, EXCHANGE)
    if not row:
        return None
    try:
        snap = await asyncio.wait_for(client(row).futures_account(), 5)
    except (BinanceError, asyncio.TimeoutError, OSError, ValueError):
        snap = None
    _snap_cache[uid] = (time.monotonic(), snap)
    return snap

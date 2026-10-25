"""Binance API — только чтение: баланс, позиции фьючерсов, реализованный P&L, комиссии, фандинг.

Ключи брокерского аккаунта Tiger.com (account.tiger.com/api) — обычные ключи Binance.
"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

import aiohttp

from .config import config

WEEK_MS = 7 * 24 * 3600 * 1000  # история доходов запрашивается окнами по 7 дней
RECV_WINDOW = 10000


class BinanceError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg

    @property
    def human(self) -> str:
        if self.code in (-2014, -2015, -1022):
            return "неверный ключ или секрет, либо IP сервера бота не добавлен в разрешённые для ключа"
        if self.code == 451:
            return "Binance блокирует доступ из страны, где запущен бот"
        if self.code == -1021:
            return "часы на сервере бота расходятся с Binance — синхронизируй время"
        return self.msg


class Binance:
    _session: aiohttp.ClientSession | None = None
    _time_offset = 0  # разница часов сервера и Binance, мс

    def __init__(self, api_key: str, secret: str):
        self.key = api_key
        self.secret = secret

    @classmethod
    def session(cls) -> aiohttp.ClientSession:
        if cls._session is None or cls._session.closed:
            cls._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return cls._session

    @classmethod
    async def close(cls) -> None:
        if cls._session:
            await cls._session.close()

    def sign(self, query: str) -> str:
        return hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    async def get(self, base: str, path: str, _retry: bool = True, **params):
        params = {k: v for k, v in params.items() if v is not None}
        params.update(recvWindow=RECV_WINDOW, timestamp=int(time.time() * 1000) + Binance._time_offset)
        query = urlencode(params)
        url = f"{base}{path}?{query}&signature={self.sign(query)}"
        async with self.session().get(url, headers={"X-MBX-APIKEY": self.key}) as r:
            if r.status == 451:
                raise BinanceError(451, "restricted location")
            data = await r.json(content_type=None)
        if isinstance(data, dict) and data.get("code", 0) < 0:
            if data["code"] == -1021 and _retry and await self._sync_time():
                return await self.get(base, path, False, **{k: v for k, v in params.items()
                                                            if k not in ("timestamp", "recvWindow")})
            raise BinanceError(data["code"], data.get("msg", "unknown error"))
        return data

    async def _sync_time(self) -> bool:
        try:
            async with self.session().get(f"{config.binance_futures}/fapi/v1/time") as r:
                server = (await r.json())["serverTime"]
        except Exception:  # noqa: BLE001
            return False
        Binance._time_offset = server - int(time.time() * 1000)
        return True

    # ---------- права ключа ----------
    async def restrictions(self) -> dict | None:
        """Права ключа. None — эндпоинт недоступен (бывает у брокерских субаккаунтов)."""
        try:
            return await self.get(config.binance_spot, "/sapi/v1/account/apiRestrictions")
        except BinanceError as err:
            if err.code in (-2014, -2015, -1022, 451):
                raise
            return None

    # ---------- фьючерсы USDT-M ----------
    async def futures_account(self) -> dict:
        acc = await self.get(config.binance_futures, "/fapi/v2/account")
        return {
            "wallet": _f(acc.get("totalWalletBalance")),
            "equity": _f(acc.get("totalMarginBalance")),
            "upnl": _f(acc.get("totalUnrealizedProfit")),
            "available": _f(acc.get("availableBalance")),
        }

    async def positions(self) -> list[dict]:
        rows = await self.get(config.binance_futures, "/fapi/v2/positionRisk")
        return [p for p in rows if _f(p.get("positionAmt"))]

    async def income(self, start_ms: int, end_ms: int) -> list[dict]:
        """REALIZED_PNL, COMMISSION, FUNDING_FEE и др. — по возрастанию времени."""
        rows: list[dict] = []
        while start_ms < end_ms:
            win_end = min(start_ms + WEEK_MS - 1, end_ms)
            page_start = start_ms
            while True:
                batch = await self.get(config.binance_futures, "/fapi/v1/income",
                                       startTime=page_start, endTime=win_end, limit=1000)
                rows += batch
                if len(batch) < 1000:
                    break
                page_start = int(batch[-1]["time"]) + 1
            start_ms = win_end + 1
        rows.sort(key=lambda r: int(r["time"]))
        return rows

    # ---------- спот ----------
    async def spot_balances(self) -> dict[str, float]:
        acc = await self.get(config.binance_spot, "/api/v3/account", omitZeroBalances="true")
        out = {}
        for b in acc.get("balances", []):
            total = _f(b.get("free")) + _f(b.get("locked"))
            if total:
                out[b["asset"]] = total
        return out


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

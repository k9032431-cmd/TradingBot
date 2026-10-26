"""Цены Binance без ключей: сначала USDT-M фьючерсы (как в Tiger Trade), если пары там нет — спот.

Цена показывается ровно с той точностью, с которой её отдаёт биржа (шаг цены пары).
"""
import asyncio
import logging
import time

import aiohttp

log = logging.getLogger(__name__)

FUTURES = "https://fapi.binance.com"
# data-api.binance.vision — зеркало спота, работает там, где основной домен блокируют
SPOT = ("https://api.binance.com", "https://data-api.binance.vision")
QUOTES = ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "TRY", "EUR")


def normalize(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("-", "").replace(" ", "")
    return s if s.endswith(QUOTES) and len(s) > 4 else s + "USDT"


def short(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def _decimals(raw: str, strip: bool) -> int:
    frac = raw.split(".")[1] if "." in raw else ""
    return len(frac.rstrip("0") if strip else frac)


class PriceClient:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._cache: dict[str, float] = {}
        self._cache_ts = 0.0
        self.prec: dict[str, int] = {}     # знаков после запятой для пары
        self.source: dict[str, str] = {}   # futures | spot
        self._spot_tick: dict[str, int] = {}  # знаков в шаге цены спотовой пары (из exchangeInfo)
        self._fut_tick: dict[str, int] = {}   # то же для фьючерсов
        self._fut_tick_ts = 0.0

    async def _get(self, bases: tuple[str, ...], path: str, params: dict | None = None):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        last_exc: Exception | None = None
        for base in bases:
            try:
                async with self.session.get(base + path, params=params) as r:
                    if r.status == 400:
                        return None  # неизвестный символ
                    r.raise_for_status()
                    return await r.json()
            except Exception as e:  # noqa: BLE001
                last_exc = e
        log.debug("%s%s недоступен: %s", bases[0], path, last_exc)
        return None

    def _remember(self, symbol: str, raw: str, source: str) -> float:
        # фьючерсы отдают цену ровно по шагу цены, спот добивает нулями до 8 знаков —
        # для спота точность берём из шага цены пары (если уже знаем)
        if source == "futures":
            self.prec[symbol] = self._fut_tick.get(symbol, _decimals(raw, strip=False))
        else:
            self.prec[symbol] = self._spot_tick.get(symbol, max(2, _decimals(raw, strip=True)))
        self.source[symbol] = source
        return float(raw)

    async def load_futures_ticks(self) -> None:
        """Шаг цены всех фьючерсных пар одним запросом, обновляем раз в 6 часов."""
        if time.monotonic() - self._fut_tick_ts < 6 * 3600 and self._fut_tick:
            return
        info = await self._get((FUTURES,), "/fapi/v1/exchangeInfo")
        for sym in (info or {}).get("symbols", []):
            for f in sym.get("filters", []):
                if f.get("filterType") == "PRICE_FILTER":
                    self._fut_tick[sym["symbol"]] = _decimals(f["tickSize"], strip=True)
        if self._fut_tick:
            self._fut_tick_ts = time.monotonic()

    async def _load_spot_tick(self, symbol: str) -> None:
        if symbol in self._spot_tick:
            return
        info = await self._get(SPOT, "/api/v3/exchangeInfo", {"symbol": symbol})
        for f in (info or {}).get("symbols", [{}])[0].get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                self._spot_tick[symbol] = _decimals(f["tickSize"], strip=True)

    async def all_prices(self, max_age: float = 3.0) -> dict[str, float]:
        """Все цены одним запросом на рынок — для алертов. Фьючерсы важнее спота."""
        if time.monotonic() - self._cache_ts < max_age and self._cache:
            return self._cache
        await self.load_futures_ticks()
        fut, spot = await asyncio.gather(self._get((FUTURES,), "/fapi/v1/ticker/price"),
                                         self._get(SPOT, "/api/v3/ticker/price"))
        prices: dict[str, float] = {}
        for rows, source in ((spot, "spot"), (fut, "futures")):
            for d in rows or []:
                prices[d["symbol"]] = self._remember(d["symbol"], d["price"], source)
        if prices:
            self._cache, self._cache_ts = prices, time.monotonic()
        return self._cache

    async def price(self, symbol: str) -> float | None:
        await self.load_futures_ticks()
        for bases, path, source in (((FUTURES,), "/fapi/v1/ticker/price", "futures"),
                                    (SPOT, "/api/v3/ticker/price", "spot")):
            data = await self._get(bases, path, {"symbol": symbol})
            if data:
                if source == "spot":
                    await self._load_spot_tick(symbol)
                return self._remember(symbol, data["price"], source)
        return None

    async def ticker24(self, symbol: str) -> dict | None:
        await self.load_futures_ticks()
        for bases, path, source in (((FUTURES,), "/fapi/v1/ticker/24hr", "futures"),
                                    (SPOT, "/api/v3/ticker/24hr", "spot")):
            data = await self._get(bases, path, {"symbol": symbol})
            if data:
                if source == "spot":
                    await self._load_spot_tick(symbol)
                self._remember(symbol, data["lastPrice"], source)
                return {**data, "source": source}
        return None

    async def close(self) -> None:
        if self.session:
            await self.session.close()


prices = PriceClient()


def fmt_price(p: float, symbol: str | None = None) -> str:
    """Цена с точностью пары, как в терминале: 80 140.50, 2 339.71, 0.00001234."""
    prec = prices.prec.get(symbol) if symbol else None
    if prec is None:
        prec = 2 if p >= 100 else 4 if p >= 1 else 8
    s = f"{p:,.{prec}f}".replace(",", " ")
    if symbol is None and "." in s:
        s = s.rstrip("0").rstrip(".")
    return s

"""Цены с Binance (публичное API, без ключей). Для фьючерсов можно поменять базовый URL."""
import logging
import time

import aiohttp

log = logging.getLogger(__name__)

# data-api.binance.vision — зеркало публичных данных, работает там, где основной домен блокируют
BASES = ("https://api.binance.com", "https://data-api.binance.vision")
QUOTES = ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "TRY", "EUR")


def normalize(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("-", "").replace(" ", "")
    return s if s.endswith(QUOTES) and len(s) > 4 else s + "USDT"


def short(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


class PriceClient:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._cache: dict[str, float] = {}
        self._cache_ts = 0.0

    async def _get(self, path: str, params: dict | None = None):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        last_exc: Exception | None = None
        for base in BASES:
            try:
                async with self.session.get(base + path, params=params) as r:
                    if r.status == 400:
                        return None  # неизвестный символ
                    r.raise_for_status()
                    return await r.json()
            except Exception as e:  # noqa: BLE001
                last_exc = e
        log.warning("Binance недоступен: %s", last_exc)
        return None

    async def all_prices(self, max_age: float = 3.0) -> dict[str, float]:
        """Все цены одним запросом — для проверки алертов."""
        if time.monotonic() - self._cache_ts < max_age and self._cache:
            return self._cache
        data = await self._get("/api/v3/ticker/price")
        if data:
            self._cache = {d["symbol"]: float(d["price"]) for d in data}
            self._cache_ts = time.monotonic()
        return self._cache

    async def price(self, symbol: str) -> float | None:
        data = await self._get("/api/v3/ticker/price", {"symbol": symbol})
        return float(data["price"]) if data else None

    async def ticker24(self, symbol: str) -> dict | None:
        return await self._get("/api/v3/ticker/24hr", {"symbol": symbol})

    async def close(self) -> None:
        if self.session:
            await self.session.close()


prices = PriceClient()


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}".replace(",", " ")
    if p >= 1:
        return f"{p:.4f}".rstrip("0").rstrip(".")
    return f"{p:.8f}".rstrip("0").rstrip(".")

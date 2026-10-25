import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, TelegramObject

from .config import config
from .db import DB
from .binance import Binance
from .handlers import alerts, common, exchange, journal, stats, tools
from .prices import prices


class AccessMiddleware(BaseMiddleware):
    """Пускаем только пользователей из ALLOWED_USERS (если список задан)."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        user = data.get("event_from_user")
        if config.allowed_users and (user is None or user.id not in config.allowed_users):
            return None
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.token:
        raise SystemExit("Укажи BOT_TOKEN в файле .env (см. .env.example)")

    db = DB(config.db_path)
    await db.connect()

    bot = Bot(config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                                         link_preview_is_disabled=True))
    dp = Dispatcher(storage=MemoryStorage(), db=db)
    dp.update.outer_middleware(AccessMiddleware())
    # common первым: /start и /menu работают из любого состояния
    dp.include_routers(common.router, exchange.router, journal.router, alerts.router, stats.router,
                       tools.router)

    await bot.set_my_commands([
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="pos", description="Открытые позиции Binance"),
        BotCommand(command="alert", description="Алерт: /alert BTC 70000"),
        BotCommand(command="p", description="Курс: /p ETH"),
        BotCommand(command="export", description="Выгрузка в CSV"),
        BotCommand(command="help", description="Помощь"),
    ])

    workers = [
        asyncio.create_task(alerts.alert_worker(bot, db, config.alert_interval)),
        asyncio.create_task(exchange.sync_worker(bot, db, config.sync_interval)),
    ]
    try:
        await dp.start_polling(bot)
    finally:
        for w in workers:
            w.cancel()
        await prices.close()
        await Binance.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

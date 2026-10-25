import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ..db import DB
from ..ui import e, h, main_menu, money, pnl

router = Router()


async def show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Колбэк — редактируем сообщение (навигация без спама), сообщение — отвечаем новым."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as err:
            if "not modified" not in str(err):
                await target.message.answer(text, reply_markup=markup)
        try:
            await target.answer()
        except TelegramBadRequest:
            pass  # колбэк уже отвечен (например, всплывающим уведомлением)
    else:
        await target.answer(text, reply_markup=markup)


def day_start(ts: float | None = None) -> int:
    t = time.localtime(ts or time.time())
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def month_start() -> int:
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1)))


async def menu_text(db: DB, user_id: int, name: str) -> str:
    user = await db.ensure_user(user_id)
    cur = user["currency"]
    balance = user["start_balance"] + await db.balance_delta(user_id)
    today = sum(r["amount"] for r in await db.entries(user_id, day_start()) if r["kind"] in ("trade", "expense"))
    month = sum(r["amount"] for r in await db.entries(user_id, month_start()) if r["kind"] in ("trade", "expense"))
    alerts = len(await db.user_alerts(user_id))
    return (
        f"{e('logo')} <b>Trading Desk</b>  ·  {h(name)}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('wallet')} Баланс:  <b>{money(balance, cur)}</b>\n"
        f"Сегодня:  {pnl(today, cur)}\n"
        f"Месяц:     {pnl(month, cur)}\n"
        f"{e('alert')} Активных алертов:  <b>{alerts}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Выбери действие</i>"
    )


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext, db: DB) -> None:
    await state.clear()
    await message.answer(await menu_text(db, message.from_user.id, message.from_user.first_name),
                         reply_markup=main_menu())


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    await show(call, await menu_text(db, call.from_user.id, call.from_user.first_name), main_menu())


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        f"{e('logo')} <b>Как пользоваться</b>\n\n"
        f"<b>Быстрый ввод</b> (без меню):\n"
        f"<code>+150 BTC пробой</code> — прибыль\n"
        f"<code>-40 ETH стоп</code> — убыток\n\n"
        f"<b>Команды</b>\n"
        f"/menu — главное меню\n"
        f"/alert BTC 70000 — алерт по цене (можно <code>+5%</code>)\n"
        f"/p SOL — курс монеты\n"
        f"/export — выгрузка в CSV\n"
        f"/emoji — узнать ID премиум-эмодзи\n"
        f"/id — твой Telegram ID"
    )


@router.message(Command("emoji"))
async def cmd_emoji(message: Message, state: FSMContext) -> None:
    await state.set_state("emoji_probe")
    await message.answer(f"{e('target')} Отправь сообщение с премиум-эмодзи — я верну их ID для <code>emoji.json</code>.")


@router.message(StateFilter("emoji_probe", None), F.text,
                lambda m: any(x.type == "custom_emoji" for x in m.entities or []))
async def emoji_ids(message: Message, state: FSMContext) -> None:
    await state.clear()
    lines = []
    for ent in message.entities:
        if ent.type == "custom_emoji":
            char = ent.extract_from(message.text)
            lines.append(f'<tg-emoji emoji-id="{ent.custom_emoji_id}">{char}</tg-emoji>  <code>{ent.custom_emoji_id}</code>')
    await message.answer("ID премиум-эмодзи:\n\n" + "\n".join(lines))

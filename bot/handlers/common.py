import re
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message

from ..config import ROOT

from ..db import DB
from ..sync import EXCHANGE, snapshot
from ..ui import e, h, main_menu, money, more_menu, pnl

router = Router()


BANNER = ROOT / "assets" / "banner.png"
CAPTION_LIMIT = 1000           # у подписи к фото лимит 1024 символа
_banner_id: str | None = None  # file_id баннера после первой загрузки — дальше не грузим заново


def _fits(text: str) -> bool:
    return len(re.sub(r"<[^>]+>", "", text)) <= CAPTION_LIMIT


async def _send(message: Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """Новый экран: широкий баннер + текст в подписи (так меню и кнопки во всю ширину)."""
    global _banner_id
    if not _fits(text):
        await message.answer(text, reply_markup=markup)
        return
    sent = await message.answer_photo(_banner_id or FSInputFile(BANNER), caption=text, reply_markup=markup)
    _banner_id = sent.photo[-1].file_id


async def show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Колбэк — меняем текущий экран на месте, сообщение — отправляем новый экран."""
    if isinstance(target, Message):
        await _send(target, text, markup)
        return
    msg = target.message
    try:
        if msg.photo and _fits(text):
            await msg.edit_caption(caption=text, reply_markup=markup)
        elif msg.text and not _fits(text):
            await msg.edit_text(text, reply_markup=markup)
        else:  # уведомление (алерт, сделка) не трогаем — открываем экран новым сообщением
            await _send(msg, text, markup)
    except TelegramBadRequest as err:
        if "not modified" not in str(err):
            await _send(msg, text, markup)
    try:
        await target.answer()
    except TelegramBadRequest:
        pass  # колбэк уже отвечен (например, всплывающим уведомлением)


def day_start(ts: float | None = None) -> int:
    t = time.localtime(ts or time.time())
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def month_start() -> int:
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1)))


async def menu_text(db: DB, user_id: int, name: str) -> str:
    user = await db.ensure_user(user_id)
    cur = user["currency"]
    today = sum(r["amount"] for r in await db.entries(user_id, day_start()) if r["kind"] in ("trade", "expense"))
    alerts = len(await db.user_alerts(user_id))
    snap = await snapshot(db, user_id)
    lines = [f"Привет, <b>{h(name)}</b> {e('logo')}", ""]
    if snap:
        lines += [f"{e('wallet')} Баланс Binance:  <b>{money(snap['equity'], 'USDT')}</b>",
                  f"{e('position')} Открытые позиции:  {pnl(snap['upnl'], 'USDT')}"]
    else:
        balance = user["start_balance"] + await db.balance_delta(user_id)
        lines.append(f"{e('wallet')} Баланс:  <b>{money(balance, cur)}</b>")
        if await db.get_api(user_id, EXCHANGE):
            lines.append(f"{e('exchange')} Binance:  <i>нет связи</i>")
    lines += [f"{e('stats')} Сегодня:  {pnl(today, cur)}",
              f"{e('alert')} Алерты:  <b>{alerts}</b>"]
    return "\n".join(lines)


async def open_menu(target: Message | CallbackQuery, db: DB) -> None:
    uid = target.from_user.id
    await show(target, await menu_text(db, uid, target.from_user.first_name),
               main_menu(bool(await db.get_api(uid, EXCHANGE))))


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext, db: DB) -> None:
    await state.clear()
    await open_menu(message, db)


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    await open_menu(call, db)


@router.callback_query(F.data == "more")
async def cb_more(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call, f"{e('settings')} <b>Ещё</b>", more_menu())


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

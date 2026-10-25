"""Курс монеты, калькулятор позиции, настройки."""
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..db import DB
from ..prices import fmt_price, normalize, prices, short
from ..ui import BLUE, GREEN, RED, back_row, btn, cancel_kb, e, h, kb, money, parse_number
from .alerts import QUICK
from .common import show

router = Router()


class Tools(StatesGroup):
    price = State()
    calc = State()
    balance = State()


# ---------- курс ----------
@router.callback_query(F.data == "price")
async def price_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Tools.price)
    q = [btn(s, f"px:{normalize(s)}", BLUE) for s in QUICK]
    await show(call, f"{e('price')} <b>Курс</b>\n\nВыбери монету или напиши тикер:", kb(q[:4], q[4:], back_row()))


async def price_card(symbol: str) -> tuple[str, object] | None:
    t = await prices.ticker24(symbol)
    if not t:
        return None
    last, chg = float(t["lastPrice"]), float(t["priceChangePercent"])
    mark = e("profit") if chg >= 0 else e("loss")
    vol = float(t["quoteVolume"])
    text = (f"{e('price')} <b>{short(symbol)}</b>  <code>{symbol}</code>\n━━━━━━━━━━━━━━━━━━\n"
            f"Цена:  <b>{fmt_price(last)}</b>\n"
            f"24ч:    {mark} <b>{chg:+.2f}%</b>\n"
            f"Макс / мин:  {fmt_price(float(t['highPrice']))} / {fmt_price(float(t['lowPrice']))}\n"
            f"Объём 24ч:  {money(vol)}")
    markup = kb([btn("Обновить", f"px:{symbol}", BLUE), btn("Алерт", f"al:for:{symbol}", GREEN, "alert")],
                [btn("Другая монета", "price", icon="price"), *back_row()])
    return text, markup


@router.callback_query(F.data.startswith("px:"))
async def price_btn(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    card = await price_card(call.data[3:])
    if card is None:
        await call.answer("Binance недоступен", show_alert=True)
        return
    await show(call, *card)


@router.message(Tools.price, F.text)
@router.message(Command("p"))
async def price_text(message: Message, state: FSMContext, command: CommandObject | None = None) -> None:
    raw = command.args if command else message.text
    if not raw:
        await message.answer("Формат: <code>/p BTC</code>")
        return
    card = await price_card(normalize(raw.split()[0]))
    if card is None:
        await message.answer(f"{e('cross')} Не нашёл <b>{h(raw)}</b> на Binance.", reply_markup=kb(back_row()))
        return
    await state.clear()
    text, markup = card
    await message.answer(text, reply_markup=markup)


# ---------- калькулятор ----------
CALC_HELP = (f"{e('calc')} <b>Калькулятор позиции</b>\n\n"
             "Считает размер позиции по риску.\n"
             "Введи: <b>депозит  риск%  вход  стоп  [тейк]</b>\n\n"
             "<code>1000 1 65000 64000 68000</code>")


@router.callback_query(F.data == "calc")
async def calc_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Tools.calc)
    await show(call, CALC_HELP, cancel_kb())


@router.message(Tools.calc, F.text)
async def calc(message: Message) -> None:
    try:
        nums = [parse_number(x) for x in message.text.replace("%", "").split()]
        depo, risk_pct, entry, stop = nums[:4]
        take = nums[4] if len(nums) > 4 else None
        if entry == stop or min(depo, risk_pct, entry, stop) <= 0:
            raise ValueError
    except (ValueError, IndexError):
        await message.answer(f"{e('cross')} Нужно 4–5 чисел.\n\n" + CALC_HELP, reply_markup=cancel_kb())
        return
    side = "LONG" if stop < entry else "SHORT"
    risk = depo * risk_pct / 100
    dist = abs(entry - stop)
    qty = risk / dist
    notional = qty * entry
    lines = [
        f"{e('calc')} <b>{side}</b>  ·  риск {risk_pct:g}%\n━━━━━━━━━━━━━━━━━━",
        f"Риск:  <b>{money(risk)}</b>",
        f"До стопа:  <b>{dist / entry * 100:.2f}%</b>",
        f"Объём:  <b>{qty:.6g}</b> монет",
        f"Позиция:  <b>{money(notional)}</b>",
        f"Плечо (мин.):  <b>x{max(notional / depo, 1):.1f}</b>",
    ]
    if take:
        rr = abs(take - entry) / dist
        lines += [f"{e('target')} R:R  <b>1 : {rr:.2f}</b>", f"Профит на тейке:  <b>{money(qty * abs(take - entry))}</b>"]
    await message.answer("\n".join(lines) + "\n\n<i>Можешь сразу ввести следующий расчёт</i>",
                         reply_markup=kb(back_row()))


# ---------- настройки ----------
CURRENCIES = ["USDT", "USD", "EUR", "RUB", "TMT"]


async def settings_text(db: DB, uid: int) -> str:
    u = await db.ensure_user(uid)
    return (f"{e('settings')} <b>Настройки</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Валюта:  <b>{u['currency']}</b>\n"
            f"Стартовый баланс:  <b>{money(u['start_balance'], u['currency'])}</b>")


def settings_kb(current: str):
    cur = [btn(c, f"set:cur:{c}", BLUE if c == current else None) for c in CURRENCIES]
    return kb(cur, [btn("Стартовый баланс", "set:bal", BLUE, "wallet")],
              [btn("Экспорт CSV", "export", icon="export"), btn("Удалить данные", "set:wipe", RED, "trash")],
              back_row())


@router.callback_query(F.data == "settings")
async def settings(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    u = await db.ensure_user(call.from_user.id)
    await show(call, await settings_text(db, call.from_user.id), settings_kb(u["currency"]))


@router.callback_query(F.data.startswith("set:cur:"))
async def set_currency(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    await db.set_user(call.from_user.id, currency=call.data.split(":")[2])
    await settings(call, state, db)


@router.callback_query(F.data == "set:bal")
async def ask_balance(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Tools.balance)
    await show(call, f"{e('wallet')} Введи стартовый баланс счёта:\n<code>1000</code>", cancel_kb("settings"))


@router.message(Tools.balance, F.text)
async def set_balance(message: Message, state: FSMContext, db: DB) -> None:
    try:
        value = parse_number(message.text)
    except ValueError:
        await message.answer(f"{e('cross')} Нужно число.", reply_markup=cancel_kb("settings"))
        return
    await state.clear()
    await db.set_user(message.from_user.id, start_balance=value)
    u = await db.ensure_user(message.from_user.id)
    await message.answer(f"{e('check')} Сохранено\n\n" + await settings_text(db, message.from_user.id),
                         reply_markup=settings_kb(u["currency"]))


@router.callback_query(F.data == "set:wipe")
async def wipe_confirm(call: CallbackQuery) -> None:
    await show(call, f"{e('trash')} <b>Удалить все сделки, расходы и алерты?</b>\nЭто нельзя отменить. "
                     f"Советую сначала сделать экспорт CSV.",
               kb([btn("Да, удалить всё", "set:wipe:yes", RED, "trash")], [btn("Нет", "settings", BLUE)]))


@router.callback_query(F.data == "set:wipe:yes")
async def wipe(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    uid = call.from_user.id
    await db.wipe_user(uid)
    await call.answer("Данные удалены", show_alert=True)
    await settings(call, state, db)

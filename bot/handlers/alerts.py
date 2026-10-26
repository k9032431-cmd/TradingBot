import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..db import DB
from ..prices import fmt_price, normalize, prices, short
from ..ui import BLUE, GREEN, RED, back_row, btn, cancel_kb, e, h, kb, parse_number
from .common import show

router = Router()
log = logging.getLogger(__name__)

QUICK = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "TON", "SUI"]


class NewAlert(StatesGroup):
    symbol = State()
    target = State()


def parse_targets(text: str, current: float) -> list[float]:
    """'70000 72k +5% -3%' -> список цен."""
    out = []
    for tok in text.replace(";", " ").replace(",", " ").split():
        if tok.endswith("%"):
            out.append(current * (1 + parse_number(tok[:-1]) / 100))
        else:
            out.append(parse_number(tok))
    if not out or any(t <= 0 for t in out):
        raise ValueError
    return out


async def create_alerts(db: DB, uid: int, symbol: str, targets: list[float], current: float) -> str:
    lines = []
    prec = prices.prec.get(symbol)
    for t in targets:
        if prec is not None:
            t = round(t, prec)  # цена алерта — по шагу цены пары, как на бирже
        direction = "above" if t > current else "below"
        await db.add_alert(uid, symbol, t, direction)
        arrow = e("up") if direction == "above" else e("down")
        diff = (t / current - 1) * 100
        lines.append(f"{arrow} <b>{fmt_price(t, symbol)}</b>  ({diff:+.2f}%)")
    return (f"{e('check')} <b>Алерт{'ы' if len(targets) > 1 else ''} на {short(symbol)} создан{'ы' if len(targets) > 1 else ''}</b>\n\n"
            + "\n".join(lines) + f"\n\nСейчас: <b>{fmt_price(current, symbol)}</b>\nЯ напишу, как только цена дойдёт.")


@router.callback_query(F.data == "alerts")
async def alerts_list(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    rows = await db.user_alerts(call.from_user.id)
    if not rows:
        text = f"{e('alert')} <b>Алерты</b>\n\nАктивных алертов нет.\nСоздай алерт — и я напишу, когда цена дойдёт до уровня."
        await show(call, text, kb([btn("Новый алерт", "al:new", GREEN, "alert")], back_row()))
        return
    allp = await prices.all_prices()
    lines = []
    for r in rows:
        cur = allp.get(r["symbol"])
        arrow = e("up") if r["direction"] == "above" else e("down")
        dist = f"  <i>{(r['target'] / cur - 1) * 100:+.2f}%</i>" if cur else ""
        lines.append(f"{arrow} <b>{short(r['symbol'])}</b>  {fmt_price(r['target'], r['symbol'])}{dist}")
    dels = [btn(f"{short(r['symbol'])} {fmt_price(r['target'], r['symbol'])}", f"al:del:{r['id']}", RED, "trash") for r in rows[:20]]
    await show(call, f"{e('alert')} <b>Алерты</b>  ·  {len(rows)}\n\n" + "\n".join(lines)
               + "\n\n<i>Нажми на алерт, чтобы удалить</i>",
               kb(*[dels[i:i + 2] for i in range(0, len(dels), 2)],
                  [btn("Новый алерт", "al:new", GREEN, "alert"), btn("Удалить все", "al:clear", RED, "cross")],
                  back_row()))


@router.callback_query(F.data == "al:new")
async def new_alert(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewAlert.symbol)
    q = [btn(s, f"al:sym:{s}", BLUE) for s in QUICK]
    await show(call, f"{e('alert')} <b>Новый алерт</b>\n\nВыбери монету или напиши тикер (например <code>PEPE</code>):",
               kb(q[:4], q[4:], [btn("Отмена", "alerts", RED, "cross")]))


async def ask_target(target: Message | CallbackQuery, state: FSMContext, raw: str) -> None:
    symbol = normalize(raw)
    current = await prices.price(symbol)
    if current is None:
        msg = target.message if isinstance(target, CallbackQuery) else target
        await msg.answer(f"{e('cross')} Не нашёл <b>{h(symbol)}</b> на Binance. Попробуй другой тикер.",
                         reply_markup=cancel_kb("alerts"))
        return
    await state.set_state(NewAlert.target)
    await state.update_data(symbol=symbol, current=current)
    await show(target, f"{e('alert')} <b>{short(symbol)}</b>  ·  сейчас <b>{fmt_price(current, symbol)}</b>\n\n"
                       f"Введи цену (можно несколько или в процентах):\n"
                       f"<code>{fmt_price(current * 1.05, symbol)}</code>\n<code>+5% -3%</code>",
               kb([btn("+2%", "al:pct:2", GREEN), btn("+5%", "al:pct:5", GREEN), btn("+10%", "al:pct:10", GREEN)],
                  [btn("−2%", "al:pct:-2", RED), btn("−5%", "al:pct:-5", RED), btn("−10%", "al:pct:-10", RED)],
                  [btn("Отмена", "alerts", icon="cross")]))


@router.callback_query(NewAlert.symbol, F.data.startswith("al:sym:"))
async def alert_sym_btn(call: CallbackQuery, state: FSMContext) -> None:
    await ask_target(call, state, call.data.split(":")[2])


@router.callback_query(F.data.startswith("al:for:"))
async def alert_for_symbol(call: CallbackQuery, state: FSMContext) -> None:
    await ask_target(call, state, call.data.split(":")[2])


@router.message(NewAlert.symbol, F.text)
async def alert_sym_text(message: Message, state: FSMContext) -> None:
    await ask_target(message, state, message.text)


@router.callback_query(NewAlert.target, F.data.startswith("al:pct:"))
async def alert_pct(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    data = await state.get_data()
    pct = float(call.data.split(":")[2])
    await state.clear()
    text = await create_alerts(db, call.from_user.id, data["symbol"], [data["current"] * (1 + pct / 100)], data["current"])
    await show(call, text, kb([btn("Все алерты", "alerts", BLUE, "alert"), *back_row()]))


@router.message(NewAlert.target, F.text)
async def alert_target(message: Message, state: FSMContext, db: DB) -> None:
    data = await state.get_data()
    try:
        targets = parse_targets(message.text, data["current"])
    except ValueError:
        await message.answer(f"{e('cross')} Не понял цену. Пример: <code>70000</code> или <code>+5%</code>",
                             reply_markup=cancel_kb("alerts"))
        return
    await state.clear()
    text = await create_alerts(db, message.from_user.id, data["symbol"], targets, data["current"])
    await show(message, text, kb([btn("Все алерты", "alerts", BLUE, "alert"), *back_row()]))


@router.message(Command("alert"))
async def cmd_alert(message: Message, command: CommandObject, db: DB) -> None:
    """/alert BTC 70000 72000 -5%"""
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/alert BTC 70000</code> или <code>/alert ETH +5% -5%</code>")
        return
    symbol = normalize(parts[0])
    current = await prices.price(symbol)
    if current is None:
        await message.answer(f"{e('cross')} Не нашёл <b>{h(symbol)}</b> на Binance.")
        return
    try:
        targets = parse_targets(parts[1], current)
    except ValueError:
        await message.answer(f"{e('cross')} Не понял цену.")
        return
    await show(message, await create_alerts(db, message.from_user.id, symbol, targets, current),
               kb([btn("Все алерты", "alerts", BLUE, "alert"), *back_row()]))


@router.callback_query(F.data.startswith("al:del:"))
async def alert_delete(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    await db.delete_alert(call.from_user.id, int(call.data.split(":")[2]))
    await alerts_list(call, state, db)


@router.callback_query(F.data == "al:clear")
async def alert_clear_confirm(call: CallbackQuery) -> None:
    await show(call, f"{e('trash')} Удалить <b>все</b> активные алерты?",
               kb([btn("Да, удалить", "al:clear:yes", RED, "trash"), btn("Нет", "alerts", BLUE)]))


@router.callback_query(F.data == "al:clear:yes")
async def alert_clear(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    await db.delete_all_alerts(call.from_user.id)
    await alerts_list(call, state, db)


# ---------- фоновая проверка ----------
async def alert_worker(bot: Bot, db: DB, interval: int) -> None:
    while True:
        try:
            active = await db.active_alerts()
            if active:
                allp = await prices.all_prices(max_age=interval / 2)
                for a in active:
                    p = allp.get(a["symbol"])
                    if p is None:
                        continue
                    hit = p >= a["target"] if a["direction"] == "above" else p <= a["target"]
                    if not hit:
                        continue
                    await db.trigger_alert(a["id"])
                    arrow = e("up") if a["direction"] == "above" else e("down")
                    word = "поднялась до" if a["direction"] == "above" else "опустилась до"
                    try:
                        await bot.send_message(
                            a["user_id"],
                            f"{e('fire')} <b>АЛЕРТ · {short(a['symbol'])}</b>\n\n"
                            f"{arrow} Цена {word} <b>{fmt_price(a['target'], a['symbol'])}</b>\n"
                            f"Сейчас: <b>{fmt_price(p, a['symbol'])}</b>",
                            reply_markup=kb(
                                [btn("Новый алерт", f"al:for:{short(a['symbol'])}", GREEN, "alert"),
                                 btn("Курс", f"px:{a['symbol']}", BLUE, "price")],
                                back_row(),
                            ),
                        )
                    except Exception as err:  # noqa: BLE001 — пользователь мог заблокировать бота
                        log.warning("alert %s: %s", a["id"], err)
        except Exception:  # noqa: BLE001
            log.exception("alert worker")
        await asyncio.sleep(interval)

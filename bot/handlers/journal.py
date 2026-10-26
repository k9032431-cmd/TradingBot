import csv
import io
import re
import time

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..db import DB
from ..prices import normalize, short
from ..ui import BLUE, GREEN, RED, back_row, btn, cancel_kb, e, h, kb, money, parse_number, pnl
from .common import show

router = Router()

EXPENSE_CATS = {
    "fee": "Комиссии", "funding": "Фандинг", "subs": "Подписки",
    "soft": "Софт / VPS", "edu": "Обучение", "other": "Другое",
}
KIND_TITLE = {"trade": "Сделка", "expense": "Расход", "deposit": "Депозит", "withdraw": "Вывод"}
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}([/-]?[A-Z]{3,5})?$", re.I)
PAGE = 8


class Entry(StatesGroup):
    amount = State()


def parse_entry(text: str, with_symbol: bool = True) -> tuple[float, str | None, str | None]:
    """'150 BTC пробой уровня' -> (150, 'BTCUSDT', 'пробой уровня')."""
    parts = text.strip().split(maxsplit=2)
    amount = parse_number(parts[0])
    symbol = note = None
    rest = parts[1:]
    if with_symbol and rest and TICKER_RE.match(rest[0]):
        symbol = normalize(rest.pop(0))
    if rest:
        note = " ".join(rest)[:200]
    return amount, symbol, note


# ---------- ввод ----------
PROMPTS = {
    "profit": f"{e('profit')} <b>Прибыль</b>\n\nВведи сумму, пару и комментарий (необязательно):\n<code>150 BTC пробой уровня</code>",
    "loss": f"{e('loss')} <b>Убыток</b>\n\nВведи сумму, пару и комментарий (необязательно):\n<code>40 ETH выбило по стопу</code>",
    "deposit": f"{e('wallet')} <b>Депозит</b>\n\nВведи сумму пополнения:\n<code>500</code>",
    "withdraw": f"{e('wallet')} <b>Вывод</b>\n\nВведи сумму вывода:\n<code>300</code>",
}


@router.callback_query(F.data.in_({"add:profit", "add:loss"}))
async def ask_trade(call: CallbackQuery, state: FSMContext) -> None:
    kind = call.data.split(":")[1]
    await state.set_state(Entry.amount)
    await state.update_data(kind=kind)
    await show(call, PROMPTS[kind], cancel_kb())


@router.callback_query(F.data == "add:expense")
async def ask_expense_cat(call: CallbackQuery) -> None:
    items = [btn(v, f"exp:{k}", BLUE) for k, v in EXPENSE_CATS.items()]
    await show(call, f"{e('expense')} <b>Расход</b>\n\nВыбери категорию:",
               kb(items[0:2], items[2:4], items[4:6], back_row()))


@router.callback_query(F.data.startswith("exp:"))
async def ask_expense(call: CallbackQuery, state: FSMContext) -> None:
    cat = call.data.split(":")[1]
    await state.set_state(Entry.amount)
    await state.update_data(kind="expense", category=EXPENSE_CATS[cat])
    await show(call, f"{e('expense')} <b>Расход · {EXPENSE_CATS[cat]}</b>\n\n"
                     f"Введи сумму и комментарий:\n<code>29 TradingView</code>", cancel_kb())


@router.callback_query(F.data == "add:cash")
async def ask_cash(call: CallbackQuery) -> None:
    await show(call, f"{e('wallet')} <b>Движение средств</b>\n\nНе влияет на P&L, только на баланс.",
               kb([btn("Депозит", "cash:deposit", GREEN, "up"), btn("Вывод", "cash:withdraw", RED, "down")],
                  back_row()))


@router.callback_query(F.data.startswith("cash:"))
async def ask_cash_amount(call: CallbackQuery, state: FSMContext) -> None:
    kind = call.data.split(":")[1]
    await state.set_state(Entry.amount)
    await state.update_data(kind=kind)
    await show(call, PROMPTS[kind], cancel_kb())


async def save_entry(message: Message, db: DB, kind: str, text: str, category: str | None = None) -> bool:
    try:
        amount, symbol, note = parse_entry(text, with_symbol=kind in ("profit", "loss"))
    except (ValueError, IndexError):
        await message.answer(f"{e('cross')} Не понял сумму. Пример: <code>150 BTC пробой</code>", reply_markup=cancel_kb())
        return False
    amount = abs(amount)
    signed = -amount if kind in ("loss", "expense", "withdraw") else amount
    db_kind = "trade" if kind in ("profit", "loss") else kind
    entry_id = await db.add_entry(message.from_user.id, db_kind, signed, symbol, category, note)

    user = await db.ensure_user(message.from_user.id)
    cur = user["currency"]
    balance = user["start_balance"] + await db.balance_delta(message.from_user.id)
    title = {"profit": "Прибыль записана", "loss": "Убыток записан"}.get(kind, f"{KIND_TITLE[db_kind]} записан")
    lines = [f"{e('check')} <b>{title}</b>", "",
             pnl(signed, cur)]
    if symbol:
        lines.append(f"Пара: <b>{short(symbol)}</b>")
    if category:
        lines.append(f"Категория: {category}")
    if note:
        lines.append(f"<i>{h(note)}</i>")
    lines += ["", f"{e('wallet')} Баланс: <b>{money(balance, cur)}</b>"]
    again = "add:profit" if kind == "profit" else "add:loss" if kind == "loss" else "add:expense" if kind == "expense" else "add:cash"
    await show(message, "\n".join(lines), kb(
        [btn("Отменить", f"del:{entry_id}:new", RED, "trash"), btn("Ещё", again, GREEN, "check")],
        back_row(),
    ))
    return True


@router.message(Entry.amount, F.text)
async def got_amount(message: Message, state: FSMContext, db: DB) -> None:
    data = await state.get_data()
    if await save_entry(message, db, data["kind"], message.text, data.get("category")):
        await state.clear()


@router.message(StateFilter(None), F.text.regexp(r"^[+\-−]\s?\d"))
async def quick_entry(message: Message, db: DB) -> None:
    text = message.text.replace("−", "-")
    kind = "loss" if text.startswith("-") else "profit"
    await save_entry(message, db, kind, text.lstrip("+- "))


# ---------- история ----------
def entry_line(r, cur: str) -> str:
    d = time.strftime("%d.%m %H:%M", time.localtime(r["created_at"]))
    what = short(r["symbol"]) if r["symbol"] else (r["category"] or KIND_TITLE.get(r["kind"], ""))
    note = f" · <i>{h(r['note'][:40])}</i>" if r["note"] else ""
    return f"<code>#{r['id']:<4}</code>{d}  {pnl(r['amount'], cur)}  {h(what)}{note}"


@router.callback_query(F.data.startswith("hist:"))
async def history_cb(call: CallbackQuery, db: DB) -> None:
    await history(call, db, int(call.data.split(":")[1]))


async def history(call: CallbackQuery, db: DB, page: int) -> None:
    uid = call.from_user.id
    cur = (await db.ensure_user(uid))["currency"]
    total = await db.count_entries(uid)
    rows = await db.entries(uid, limit=PAGE, offset=page * PAGE)
    if not rows:
        await show(call, f"{e('history')} <b>История пуста</b>\n\nДобавь первую сделку через меню "
                         f"или просто напиши <code>+150 BTC</code>", kb(back_row()))
        return
    pages = (total + PAGE - 1) // PAGE
    text = f"{e('history')} <b>История</b>  ·  стр. {page + 1}/{pages}\n\n" + "\n".join(entry_line(r, cur) for r in rows)
    dels = [btn(f"#{r['id']}", f"del:{r['id']}:{page}", RED, "trash") for r in rows]
    nav = []
    if page > 0:
        nav.append(btn("Назад", f"hist:{page - 1}", BLUE))
    if page < pages - 1:
        nav.append(btn("Дальше", f"hist:{page + 1}", BLUE))
    rows_kb = [dels[i:i + 4] for i in range(0, len(dels), 4)]
    await show(call, text + "\n\n<i>Нажми на номер, чтобы удалить запись</i>",
               kb(*rows_kb, *([nav] if nav else []), [btn("CSV", "export", icon="export"), *back_row()]))


@router.callback_query(F.data.startswith("del:"))
async def delete_entry(call: CallbackQuery, db: DB) -> None:
    _, entry_id, back = call.data.split(":")
    ok = await db.delete_entry(call.from_user.id, int(entry_id))
    await call.answer("Удалено" if ok else "Уже удалено", show_alert=False)
    if back == "new":
        await show(call, f"{e('trash')} Запись #{entry_id} отменена.", kb(back_row()))
    else:
        await history(call, db, int(back))


# ---------- экспорт ----------
@router.message(Command("export"))
@router.callback_query(F.data == "export")
async def export(event: Message | CallbackQuery, db: DB) -> None:
    uid = event.from_user.id
    rows = await db.entries(uid)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "date", "type", "amount", "symbol", "category", "note"])
    for r in reversed(rows):
        w.writerow([r["id"], time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"])),
                    r["kind"], f"{r['amount']:.2f}", r["symbol"] or "", r["category"] or "", r["note"] or ""])
    msg = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    file = BufferedInputFile(("﻿" + buf.getvalue()).encode("utf-8"),
                             filename=f"trades_{time.strftime('%Y%m%d')}.csv")
    await msg.answer_document(file, caption=f"{e('export')} Выгрузка: {len(rows)} записей")

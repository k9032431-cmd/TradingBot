import time
from collections import defaultdict

from aiogram import F, Router
from aiogram.types import CallbackQuery

from ..db import DB
from ..prices import short
from ..ui import BLUE, back_row, btn, e, h, kb, money, pnl
from .common import day_start, month_start, show

router = Router()

PERIODS = {"today": "Сегодня", "week": "Неделя", "month": "Месяц", "all": "Всё время"}


def period_since(p: str) -> int:
    if p == "today":
        return day_start()
    if p == "week":
        return day_start(time.time() - time.localtime().tm_wday * 86400)
    if p == "month":
        return month_start()
    return 0


@router.callback_query(F.data.startswith("stats:"))
async def stats(call: CallbackQuery, db: DB) -> None:
    period = call.data.split(":")[1]
    uid = call.from_user.id
    user = await db.ensure_user(uid)
    cur = user["currency"]
    rows = await db.entries(uid, period_since(period))

    trades = [r["amount"] for r in rows if r["kind"] == "trade"]
    wins = [x for x in trades if x > 0]
    losses = [x for x in trades if x < 0]
    expenses = sum(r["amount"] for r in rows if r["kind"] == "expense")
    gross_win, gross_loss = sum(wins), sum(losses)
    net = gross_win + gross_loss + expenses
    winrate = len(wins) / len(trades) * 100 if trades else 0
    pf = gross_win / abs(gross_loss) if gross_loss else None

    tabs = [btn(("• " if k == period else "") + v, f"stats:{k}", BLUE if k == period else None)
            for k, v in PERIODS.items()]
    markup = kb(tabs[:2], tabs[2:], [btn("История", "hist:0", BLUE, "history"), *back_row()])

    head = f"{e('stats')} <b>Статистика · {PERIODS[period]}</b>\n━━━━━━━━━━━━━━━━━━\n"
    if not rows:
        await show(call, head + "Нет записей за этот период.", markup)
        return

    bar_len = 10
    filled = round(winrate / 100 * bar_len)
    bar = "▰" * filled + "▱" * (bar_len - filled)

    lines = [
        f"Чистый результат:  {pnl(net, cur)}",
        "",
        f"{e('profit')} Прибыль:   <b>{money(gross_win, cur)}</b>  ({len(wins)})",
        f"{e('loss')} Убытки:     <b>{money(abs(gross_loss), cur)}</b>  ({len(losses)})",
        f"{e('expense')} Расходы:   <b>{money(abs(expenses), cur)}</b>",
        "",
        f"{e('target')} Винрейт:  <b>{winrate:.0f}%</b>  {bar}",
        f"Сделок: <b>{len(trades)}</b>",
    ]
    if wins:
        lines.append(f"Средняя прибыль: <b>{money(gross_win / len(wins), cur)}</b>")
    if losses:
        lines.append(f"Средний убыток:  <b>{money(abs(gross_loss) / len(losses), cur)}</b>")
    if pf is not None:
        lines.append(f"Профит-фактор:   <b>{pf:.2f}</b>")
    if trades:
        lines.append(f"Лучшая / худшая: <b>{money(max(trades), cur, True)}</b> / <b>{money(min(trades), cur, True)}</b>")

    by_symbol: dict[str, float] = defaultdict(float)
    for r in rows:
        if r["kind"] == "trade" and r["symbol"]:
            by_symbol[r["symbol"]] += r["amount"]
    if by_symbol:
        top = sorted(by_symbol.items(), key=lambda x: -abs(x[1]))[:5]
        lines += ["", f"{e('chart')} <b>По парам</b>"] + [f"{h(short(s))}:  {pnl(v, cur)}" for s, v in top]

    cats: dict[str, float] = defaultdict(float)
    for r in rows:
        if r["kind"] == "expense":
            cats[r["category"] or "Другое"] += r["amount"]
    if cats:
        lines += ["", f"{e('expense')} <b>Расходы по категориям</b>"] + \
                 [f"{h(c)}:  {money(abs(v), cur)}" for c, v in sorted(cats.items(), key=lambda x: x[1])]

    balance = user["start_balance"] + await db.balance_delta(uid)
    lines += ["━━━━━━━━━━━━━━━━━━", f"{e('wallet')} Баланс: <b>{money(balance, cur)}</b>"]
    await show(call, head + "\n".join(lines), markup)

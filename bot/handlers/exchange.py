"""Подключение Binance через API-ключ Tiger.com: баланс, позиции, автоимпорт сделок."""
import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..binance import Binance, BinanceError
from ..crypto import encrypt
from ..db import DB
from ..prices import fmt_price, prices, short
from ..sync import EXCHANGE, STABLE, client, snapshot, sync_user
from ..ui import BLUE, GREEN, RED, back_row, btn, cancel_kb, e, h, kb, money, pnl
from .common import show

router = Router()
log = logging.getLogger(__name__)


class Connect(StatesGroup):
    keys = State()


HOWTO = (
    f"{e('link')} <b>Подключение Binance (Tiger.com)</b>\n\n"
    "1. Открой <b>account.tiger.com/api</b> → «Новый API-ключ»\n"
    "2. Оставь <b>только «Права на чтение»</b> — без торговли и фьючерсов\n"
    "3. Отправь сюда одним сообщением ключ и секрет:\n"
    "<code>API_KEY\nSECRET_KEY</code>\n\n"
    f"{e('lock')} Сообщение с ключами я сразу удалю, секрет хранится зашифрованным. "
    "Ключ с правами на торговлю бот не примет — так твои деньги в безопасности.\n\n"
    "Если в ключе включена привязка к IP — добавь IP сервера, где работает бот."
)


@router.callback_query(F.data == "ex")
async def exchange_screen(call: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    row = await db.get_api(call.from_user.id, EXCHANGE)
    if not row:
        await show(call, HOWTO, kb([btn("Ввести ключ", "ex:connect", GREEN, "link")], back_row()))
        return
    await call.answer("Загружаю…")
    lines = [f"{e('exchange')} <b>Binance · Tiger.com</b>\n"]
    try:
        api = client(row)
        acc, spot = await asyncio.gather(api.futures_account(), api.spot_balances(), return_exceptions=True)
        if isinstance(acc, Exception):
            raise acc
        lines += [
            "<b>Фьючерсы USDT-M</b>",
            f"Эквити:  <b>{money(acc['equity'], 'USDT')}</b>",
            f"Кошелёк:  {money(acc['wallet'], 'USDT')}",
            f"Доступно:  {money(acc['available'], 'USDT')}",
            f"Открытый P&L:  {pnl(acc['upnl'], 'USDT')}",
        ]
        if isinstance(spot, dict) and spot:
            allp = await prices.all_prices()
            total = sum(q if a in STABLE else q * allp.get(a + "USDT", 0) for a, q in spot.items())
            top = sorted(spot.items(), key=lambda x: -(x[1] if x[0] in STABLE else x[1] * allp.get(x[0] + "USDT", 0)))
            lines += ["", "<b>Спот</b>", f"≈ <b>{money(total, 'USDT')}</b>  ·  "
                      + ", ".join(f"{a} {q:.6g}" for a, q in top[:4])]
    except BinanceError as err:
        lines.append(f"{e('cross')} Ошибка Binance: {h(err.human)}")
    except Exception as err:  # noqa: BLE001
        lines.append(f"{e('cross')} Binance не отвечает: {h(type(err).__name__)}")
    synced = row["last_sync_ms"]
    if synced:
        lines += ["", f"<i>Сделки синхронизированы: {time.strftime('%d.%m %H:%M', time.localtime(synced / 1000))}</i>"]
    await show(call, "\n".join(lines), kb(
        [btn("Позиции", "ex:pos", BLUE, "position"), btn("Синхронизировать", "ex:sync", GREEN, "sync")],
        [btn("Обновить", "ex", BLUE), btn("Отключить", "ex:off", RED, "cross")],
        back_row(),
    ))


@router.callback_query(F.data == "ex:connect")
async def ask_keys(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Connect.keys)
    await show(call, HOWTO, cancel_kb("ex"))


@router.message(Connect.keys, F.text)
async def got_keys(message: Message, state: FSMContext, db: DB) -> None:
    parts = message.text.split()
    try:
        await message.delete()  # не оставляем секрет в переписке
    except TelegramBadRequest:
        pass
    if len(parts) != 2 or min(len(p) for p in parts) < 20:
        await message.answer(f"{e('cross')} Нужно два значения: API-ключ и секрет, через пробел или с новой строки.",
                             reply_markup=cancel_kb("ex"))
        return
    api_key, secret = parts
    wait = await message.answer(f"{e('sync')} Проверяю ключ…")
    api = Binance(api_key, secret)
    try:
        perms = await api.restrictions()
        if perms and (perms.get("enableSpotAndMarginTrading") or perms.get("enableFutures")
                      or perms.get("enableWithdrawals") or perms.get("enableMargin")):
            await wait.edit_text(
                f"{e('lock')} <b>У ключа есть права на торговлю или вывод.</b>\n\n"
                "Для безопасности бот принимает только ключи с правом <b>чтения</b>. "
                "Создай новый ключ на account.tiger.com/api только с «Права на чтение».",
                reply_markup=cancel_kb("ex"))
            return
        await api.futures_account()
    except BinanceError as err:
        await wait.edit_text(f"{e('cross')} Binance отклонил ключ: {h(err.human)}", reply_markup=cancel_kb("ex"))
        return
    except Exception as err:  # noqa: BLE001
        await wait.edit_text(f"{e('cross')} Не удалось связаться с Binance ({h(type(err).__name__)}). Попробуй позже.",
                             reply_markup=cancel_kb("ex"))
        return

    await state.clear()
    uid = message.from_user.id
    await db.set_api(uid, EXCHANGE, api_key, encrypt(secret))
    await wait.edit_text(f"{e('check')} Ключ подключён. Импортирую сделки за 30 дней…")
    try:
        res = await sync_user(db, await db.get_api(uid, EXCHANGE), initial=True)
        trades = res["trades"]
        total = sum(t["usd"] for t in trades)
        text = (f"{e('check')} <b>Binance подключён</b>\n\n"
                f"Импортировано сделок:  <b>{len(trades)}</b>\n"
                f"Реализованный P&L:  {pnl(total, 'USDT')}\n"
                f"Комиссии:  {pnl(res['fees'], 'USDT')}\n"
                f"Фандинг:  {pnl(res['funding'], 'USDT')}\n\n"
                "Теперь новые закрытые сделки будут попадать в журнал сами, и я буду присылать уведомление.")
    except Exception as err:  # noqa: BLE001
        log.exception("first import")
        text = f"{e('check')} Ключ подключён, но импорт истории не удался: {h(str(err))}. Попробую позже автоматически."
    await wait.delete()
    await show(message, text, kb(
        [btn("Статистика", "stats:month", BLUE, "stats"), btn("Позиции", "ex:pos", BLUE, "position")], back_row()))


async def positions_view(db: DB, uid: int):
    row = await db.get_api(uid, EXCHANGE)
    if not row:
        return HOWTO, kb([btn("Ввести ключ", "ex:connect", GREEN, "link")], back_row())
    markup = kb([btn("Обновить", "ex:pos", BLUE, "sync"), btn("Binance", "ex", BLUE, "exchange")], back_row())
    try:
        rows = await client(row).positions()
    except BinanceError as err:
        return f"{e('cross')} Ошибка Binance: {h(err.human)}", markup
    except Exception as err:  # noqa: BLE001
        return f"{e('cross')} Binance не отвечает ({h(type(err).__name__)})", markup
    await prices.load_futures_ticks()  # цены в позициях — с шагом цены пары, как в терминале
    head = f"{e('position')} <b>Открытые позиции</b>  ·  {time.strftime('%H:%M:%S')}\n\n"
    if not rows:
        return head + "Открытых позиций нет.", markup
    blocks, total = [], sum(float(p["unRealizedProfit"]) for p in rows)
    rows = sorted(rows, key=lambda p: -abs(float(p["unRealizedProfit"])))
    for p in rows[:6]:  # больше не влезет в подпись к баннеру
        amt = float(p["positionAmt"])
        entry, mark = float(p["entryPrice"]), float(p["markPrice"])
        upnl = float(p["unRealizedProfit"])
        lev = float(p.get("leverage") or 1)
        margin = abs(amt) * entry / lev if lev else 0
        roe = upnl / margin * 100 if margin else 0
        liq = float(p.get("liquidationPrice") or 0)
        side = f"{e('profit')} LONG" if amt > 0 else f"{e('loss')} SHORT"
        blocks.append(
            f"{side}  <b>{short(p['symbol'])}</b>  x{lev:g}\n"
            f"Объём {abs(amt):g}  ·  {money(abs(amt) * mark, 'USDT')}\n"
            f"Вход {fmt_price(entry, p['symbol'])} → {fmt_price(mark, p['symbol'])}\n"
            f"P&L  {pnl(upnl)}  ({roe:+.1f}%)"
            + (f"\nЛиквидация  {fmt_price(liq, p['symbol'])}" if liq else "")
        )
    more = f"\n\n<i>…и ещё {len(rows) - 6}</i>" if len(rows) > 6 else ""
    return head + "\n\n".join(blocks) + more + f"\n\nИтого:  {pnl(total, 'USDT')}", markup


@router.callback_query(F.data == "ex:pos")
async def positions(call: CallbackQuery, db: DB) -> None:
    await show(call, *await positions_view(db, call.from_user.id))


@router.message(Command("pos"))
async def cmd_positions(message: Message, db: DB) -> None:
    text, markup = await positions_view(db, message.from_user.id)
    await show(message, text, markup)


@router.callback_query(F.data == "ex:sync")
async def manual_sync(call: CallbackQuery, db: DB) -> None:
    row = await db.get_api(call.from_user.id, EXCHANGE)
    if not row:
        await call.answer("Binance не подключён", show_alert=True)
        return
    try:
        res = await sync_user(db, row)
    except BinanceError as err:
        await call.answer(f"Ошибка: {err.human}"[:190], show_alert=True)
        return
    except Exception as err:  # noqa: BLE001
        await call.answer(f"Binance не отвечает ({type(err).__name__})", show_alert=True)
        return
    n = len(res["trades"])
    await call.answer(f"Новых сделок: {n}" if n else "Новых сделок нет", show_alert=True)


@router.callback_query(F.data == "ex:off")
async def disconnect_confirm(call: CallbackQuery) -> None:
    await show(call, f"{e('cross')} <b>Отключить Binance?</b>\n\nКлюч будет удалён из бота. "
                     "Уже импортированные сделки останутся в журнале.\n"
                     "Сам ключ можно удалить на account.tiger.com/api.",
               kb([btn("Да, отключить", "ex:off:yes", RED, "cross")], [btn("Нет", "ex", BLUE)]))


@router.callback_query(F.data == "ex:off:yes")
async def disconnect(call: CallbackQuery, db: DB) -> None:
    await db.delete_api(call.from_user.id, EXCHANGE)
    await show(call, f"{e('check')} Binance отключён.", kb(back_row()))


# ---------- фоновая синхронизация ----------
async def sync_worker(bot: Bot, db: DB, interval: int) -> None:
    while True:
        for row in await db.all_apis(EXCHANGE):
            try:
                res = await sync_user(db, row)
            except Exception as err:  # noqa: BLE001 — сеть/ключ отозван: попробуем в следующий раз
                log.warning("sync %s: %s", row["user_id"], err)
                continue
            for t in res["trades"][-10:]:
                mark = e("fire") if t["usd"] > 0 else e("down")
                try:
                    await bot.send_message(
                        row["user_id"],
                        f"{mark} <b>Сделка закрыта · {short(t['symbol'])}</b>\n"
                        f"{pnl(t['usd'], 'USDT')}\n<i>Добавлено в журнал</i>",
                        reply_markup=kb([btn("Статистика", "stats:today", BLUE, "stats"),
                                         btn("Позиции", "ex:pos", BLUE, "position")], back_row()),
                    )
                except Exception as err:  # noqa: BLE001
                    log.warning("notify %s: %s", row["user_id"], err)
        await asyncio.sleep(interval)

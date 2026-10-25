"""Внешний вид: эмодзи (обычные или премиум), цветные кнопки, форматирование."""
import json
from html import escape

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import ROOT

# ключ -> обычный эмодзи (фолбэк, если ID премиум-эмодзи не задан в emoji.json)
EMOJI = {
    "logo": "💎", "profit": "🟢", "loss": "🔴", "expense": "💸", "stats": "📊",
    "history": "📒", "alert": "🔔", "price": "📈", "calc": "🧮", "settings": "⚙️",
    "back": "◀️", "up": "🚀", "down": "📉", "fire": "🔥", "check": "✅", "cross": "❌",
    "trash": "🗑", "wallet": "💰", "chart": "📊", "target": "🎯", "export": "📤",
}


def _load_ids() -> dict[str, str]:
    path = ROOT / "emoji.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: str(v) for k, v in data.items() if k in EMOJI and v}


PREMIUM = _load_ids()

BLUE, GREEN, RED = ButtonStyle.PRIMARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER


def e(key: str) -> str:
    """Эмодзи для текста сообщения (HTML)."""
    char = EMOJI[key]
    if key in PREMIUM:
        return f'<tg-emoji emoji-id="{PREMIUM[key]}">{char}</tg-emoji>'
    return char


def btn(text: str, cb: str, style: ButtonStyle | None = None, icon: str | None = None) -> InlineKeyboardButton:
    """Кнопка: премиум-эмодзи ставится иконкой, иначе обычный эмодзи в начале текста."""
    if icon and icon in PREMIUM:
        return InlineKeyboardButton(text=text, callback_data=cb, style=style,
                                    icon_custom_emoji_id=PREMIUM[icon])
    if icon:
        text = f"{EMOJI[icon]} {text}"
    return InlineKeyboardButton(text=text, callback_data=cb, style=style)


def kb(*rows: list[InlineKeyboardButton] | InlineKeyboardButton) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r if isinstance(r, list) else [r] for r in rows])


def back_row(cb: str = "menu") -> list[InlineKeyboardButton]:
    return [btn("Меню", cb, icon="back")]


def main_menu() -> InlineKeyboardMarkup:
    return kb(
        [btn("Прибыль", "add:profit", GREEN, "profit"), btn("Убыток", "add:loss", RED, "loss")],
        [btn("Расход", "add:expense", RED, "expense"), btn("Депозит / вывод", "add:cash", GREEN, "wallet")],
        [btn("Статистика", "stats:month", BLUE, "stats"), btn("История", "hist:0", BLUE, "history")],
        [btn("Алерты", "alerts", BLUE, "alert"), btn("Курс", "price", BLUE, "price")],
        [btn("Калькулятор", "calc", icon="calc"), btn("Настройки", "settings", icon="settings")],
    )


def cancel_kb(cb: str = "menu") -> InlineKeyboardMarkup:
    return kb([btn("Отмена", cb, RED, "cross")])


def money(x: float, cur: str = "", sign: bool = False) -> str:
    s = f"{abs(x):,.2f}".replace(",", " ")
    if sign:
        s = ("+" if x > 0 else "−" if x < 0 else "") + s
    elif x < 0:
        s = "−" + s
    return f"{s} {cur}".strip()


def pnl(x: float, cur: str = "") -> str:
    mark = e("profit") if x > 0 else e("loss") if x < 0 else "⚪️"
    return f"{mark} <b>{money(x, cur, sign=True)}</b>"


def h(text: str | None) -> str:
    return escape(text or "")


def parse_number(s: str) -> float:
    s = s.strip().replace(" ", "").replace(",", ".").replace("$", "")
    k = 1.0
    if s.lower().endswith("k"):
        s, k = s[:-1], 1000.0
    return float(s) * k

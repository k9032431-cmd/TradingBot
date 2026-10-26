"""Генерирует assets/banner.png — широкая шапка для экранов бота.  python tools/make_banner.py"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 440
ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = Path("/usr/share/fonts/opentype/inter")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for path in (FONT_DIR / name, Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def main() -> None:
    img = Image.new("RGB", (W, H))
    px = img.load()
    # тёмный градиент: глубокий синий -> почти чёрный
    for y in range(H):
        for x in range(W):
            t = x / W
            v = y / H
            px[x, y] = (int(10 + 8 * t), int(14 + 14 * (1 - v) * (1 - t)), int(28 + 30 * (1 - v) * (1 - t)))

    d = ImageDraw.Draw(img, "RGBA")
    # тонкая сетка
    for x in range(0, W, 64):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 10))
    for y in range(0, H, 64):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 10))

    # свечи и линия графика справа
    random.seed(7)
    price, pts = 235.0, []
    for i in range(23):
        x = 790 + i * 20
        o = price
        price += random.uniform(-14, 19)
        c = price
        hi, lo = max(o, c) + random.uniform(3, 12), min(o, c) - random.uniform(3, 12)
        up = c >= o
        col = (34, 197, 94, 200) if up else (239, 68, 68, 190)
        d.line([(x + 6, H - hi), (x + 6, H - lo)], fill=col, width=2)
        d.rectangle([x, H - max(o, c), x + 12, H - min(o, c)], fill=col)
        pts.append((x + 6, H - c - 40))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.line(pts, fill=(56, 189, 248, 255), width=6, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    img.paste(glow, (0, 0), glow)
    d = ImageDraw.Draw(img, "RGBA")
    d.line(pts, fill=(125, 211, 252, 255), width=3, joint="curve")

    # затемнение слева под текст
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for x in range(760):
        sd.line([(x, 0), (x, H)], fill=(6, 10, 22, int(235 * (1 - x / 760) ** 1.4)))
    img.paste(shade, (0, 0), shade)
    d = ImageDraw.Draw(img, "RGBA")

    # ромб-логотип
    cx, cy, r = 112, 168, 34
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=(56, 189, 248, 255))
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy)], fill=(125, 211, 252, 255))
    d.polygon([(cx - r, cy), (cx, cy + r), (cx, cy)], fill=(14, 116, 144, 255))

    d.text((170, 128), "TRADING DESK", font=font("Inter-Bold.otf", 76), fill=(240, 246, 255))
    d.text((174, 228), "Журнал сделок  ·  Алерты  ·  Binance", font=font("Inter-Medium.otf", 34),
           fill=(148, 163, 184))
    # акцентная полоска
    d.rounded_rectangle([174, 298, 334, 304], radius=3, fill=(34, 197, 94))
    d.rounded_rectangle([344, 298, 404, 304], radius=3, fill=(56, 189, 248))
    d.rounded_rectangle([414, 298, 444, 304], radius=3, fill=(239, 68, 68))

    out = ROOT / "assets" / "banner.png"
    img.save(out, optimize=True)
    print(out, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()

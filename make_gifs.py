"""Генерирует гифки вращающегося колеса: по одной на каждый приз.
Колесо останавливается ровно на выпавшем призе (стрелка сверху).
Запуск: python make_gifs.py
Если хотите свою гифку — просто положите её в gifs/spin_0.gif, spin_1.gif ... (по номеру приза).
"""
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config

SIZE = 480
FRAMES = 40
FRAME_MS = 70           # итого ~2.8 сек вращения + пауза на последнем кадре
LAST_PAUSE_MS = 2000
COLORS = ["#E76F51", "#F4A261", "#E9C46A", "#2A9D8F", "#457B9D", "#9B5DE5", "#F15BB5", "#00BBF9"]
OUT = Path(__file__).parent / "gifs"


def font(size):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def short(name, n=18):
    return name if len(name) <= n else name[: n - 1] + "…"


def draw_wheel(rotation_deg):
    """rotation_deg — поворот колеса по часовой стрелке."""
    S = 2  # суперсэмплинг для гладких краёв
    W = SIZE * S
    img = Image.new("RGB", (W, W), "white")
    d = ImageDraw.Draw(img)
    cx = cy = W // 2
    r = int(W * 0.44)
    n = len(config.PRIZES)
    seg = 360 / n

    for i, p in enumerate(config.PRIZES):
        # сегмент 0 по центру сверху при rotation=0
        start = -90 - seg / 2 + i * seg + rotation_deg
        d.pieslice([cx - r, cy - r, cx + r, cy + r], start, start + seg,
                   fill=COLORS[i % len(COLORS)], outline="white", width=4 * S)
        mid = math.radians(start + seg / 2)
        tx = cx + math.cos(mid) * r * 0.55
        ty = cy + math.sin(mid) * r * 0.55
        txt = short(p["name"])
        f = font(13 * S)
        tmp = Image.new("RGBA", (r, 40 * S), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        bbox = td.textbbox((0, 0), txt, font=f)
        td.text(((r - (bbox[2] - bbox[0])) / 2, 6 * S), txt, font=f, fill="white")
        rot = tmp.rotate(-(start + seg / 2), expand=True, resample=Image.BICUBIC)
        img.paste(rot, (int(tx - rot.width / 2), int(ty - rot.height / 2)), rot)

    # ободок и центр
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline="#333333", width=8 * S)
    d.ellipse([cx - 28 * S, cy - 28 * S, cx + 28 * S, cy + 28 * S], fill="white", outline="#333333", width=5 * S)
    # стрелка сверху
    ax, ay = cx, cy - r - 4 * S
    d.polygon([(ax - 18 * S, ay - 34 * S), (ax + 18 * S, ay - 34 * S), (ax, ay + 12 * S)],
              fill="#D62828", outline="#333333")
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def ease_out(t):
    return 1 - (1 - t) ** 3


def main():
    OUT.mkdir(exist_ok=True)
    n = len(config.PRIZES)
    seg = 360 / n
    for idx in range(n):
        # чтобы сегмент idx оказался сверху, колесо надо повернуть на -idx*seg (mod 360)
        final = (-idx * seg) % 360
        total = 360 * 4 + final
        frames = []
        for f in range(FRAMES):
            t = ease_out(f / (FRAMES - 1))
            frames.append(draw_wheel(total * t))
        durations = [FRAME_MS] * (FRAMES - 1) + [LAST_PAUSE_MS]
        path = OUT / f"spin_{idx}.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations,
                       loop=0, optimize=True)
        print("OK", path)


if __name__ == "__main__":
    main()

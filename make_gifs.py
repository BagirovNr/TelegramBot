"""Генерирует гифку вращающегося колеса розыгрыша: gifs/draw.gif
Запуск: python make_gifs.py   (или положите свою гифку в gifs/draw.gif)
"""
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 480
FRAMES = 44
FRAME_MS = 70
LAST_PAUSE_MS = 2000
SEGMENTS = 10
LABEL = "THE CHOCO"
# (цвет сегмента, цвет текста) — шоколадная палитра
PALETTE = [("#4E2A1E", "#F6E7D3"), ("#C68E5B", "#3A1F14"), ("#8B5A2B", "#F6E7D3"), ("#F0D9B5", "#3A1F14")]
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


def draw_wheel(rotation_deg):
    S = 2
    W = SIZE * S
    img = Image.new("RGB", (W, W), "white")
    d = ImageDraw.Draw(img)
    cx = cy = W // 2
    r = int(W * 0.44)
    seg = 360 / SEGMENTS
    f = font(13 * S)
    for i in range(SEGMENTS):
        fill, txtcol = PALETTE[i % len(PALETTE)]
        start = -90 - seg / 2 + i * seg + rotation_deg
        d.pieslice([cx - r, cy - r, cx + r, cy + r], start, start + seg, fill=fill, outline="white", width=4 * S)
        mid = math.radians(start + seg / 2)
        tx = cx + math.cos(mid) * r * 0.58
        ty = cy + math.sin(mid) * r * 0.58
        tmp = Image.new("RGBA", (r, 40 * S), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        bb = td.textbbox((0, 0), LABEL, font=f)
        td.text(((r - (bb[2] - bb[0])) / 2, 6 * S), LABEL, font=f, fill=txtcol)
        rot = tmp.rotate(-(start + seg / 2), expand=True, resample=Image.BICUBIC)
        img.paste(rot, (int(tx - rot.width / 2), int(ty - rot.height / 2)), rot)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline="#2B1810", width=8 * S)
    d.ellipse([cx - 28 * S, cy - 28 * S, cx + 28 * S, cy + 28 * S], fill="white", outline="#2B1810", width=5 * S)
    ax, ay = cx, cy - r - 4 * S
    d.polygon([(ax - 18 * S, ay - 34 * S), (ax + 18 * S, ay - 34 * S), (ax, ay + 12 * S)], fill="#B23A2E", outline="#2B1810")
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    OUT.mkdir(exist_ok=True)
    total = 360 * 4 + 137
    frames = [draw_wheel(total * (1 - (1 - f / (FRAMES - 1)) ** 3)) for f in range(FRAMES)]
    durations = [FRAME_MS] * (FRAMES - 1) + [LAST_PAUSE_MS]
    path = OUT / "draw.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print("OK", path)


if __name__ == "__main__":
    main()

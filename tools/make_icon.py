"""生成应用图标 assets/icon.ico（开发时运行一次即可）。

使用 Pillow + numpy（DSH 运行时已内置）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

SIZE = 1024
SCALE = SIZE / 64.0


def gradient() -> Image.Image:
    y, x = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    t = np.clip((x / (SIZE - 1)) * 0.55 + (y / (SIZE - 1)) * 0.45, 0.0, 1.0)

    c1 = np.array([109, 124, 255], dtype=np.float32)
    c2 = np.array([139, 92, 246], dtype=np.float32)
    c3 = np.array([47, 224, 200], dtype=np.float32)

    left = t < 0.55
    k = np.where(left, t / 0.55, (t - 0.55) / 0.45)[..., None]
    a = np.where(left[..., None], c1, c2)
    b = np.where(left[..., None], c2, c3)
    rgb = a + (b - a) * k
    rgba = np.dstack([rgb, np.full((SIZE, SIZE), 255, dtype=np.float32)])
    return Image.fromarray(rgba.astype(np.uint8), "RGBA")


def rounded_mask() -> Image.Image:
    mask = Image.new("L", (SIZE, SIZE), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=int(16 * SCALE), fill=255)
    return mask


def glyph() -> Image.Image:
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width = max(1, int(3.4 * SCALE))
    points_top = [(18, 25), (32, 18), (46, 25), (32, 32), (18, 25)]
    points_mid = [(18, 32.5), (32, 39.5), (46, 32.5)]
    points_bottom = [(18, 39.5), (32, 46.5), (46, 39.5)]
    for points in (points_top, points_mid, points_bottom):
        scaled = [(x * SCALE, y * SCALE) for x, y in points]
        draw.line(scaled, fill=(255, 255, 255, 255), width=width, joint="curve")
        radius = width / 2
        for px, py in scaled:
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=(255, 255, 255, 255))
    return layer


def main() -> None:
    base = gradient()
    base.putalpha(rounded_mask())

    # 顶部高光
    highlight = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(highlight)
    hdraw.ellipse([-SIZE * 0.25, -SIZE * 0.55, SIZE * 1.25, SIZE * 0.28], fill=(255, 255, 255, 28))
    base = Image.alpha_composite(base, Image.composite(highlight, Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0)), rounded_mask()))
    base = Image.alpha_composite(base, glyph())

    icon = base.resize((256, 256), Image.LANCZOS)
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    ico = ASSETS / "icon.ico"
    icon.save(ico, format="ICO", sizes=sizes)
    icon.save(ASSETS / "icon.png")
    print("saved", ico)


if __name__ == "__main__":
    main()

"""Значки оболочки (shell/icons): синий квадрат со слайдом и растущими столбиками.

Запуск: ``uv run python tools/make_icons.py`` (нужен Pillow — он уже есть как зависимость python-pptx).
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "shell" / "icons"
SIZE = 1024
BLUE, DARK, WHITE = (34, 139, 230, 255), (24, 100, 171, 255), (255, 255, 255, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((32, 32, SIZE - 32, SIZE - 32), radius=200, fill=BLUE)
    # слайд
    d.rounded_rectangle((176, 232, SIZE - 176, SIZE - 296), radius=48, fill=WHITE)
    # подставка слайда
    d.rectangle((SIZE // 2 - 24, SIZE - 296, SIZE // 2 + 24, SIZE - 196), fill=WHITE)
    d.rounded_rectangle((SIZE // 2 - 150, SIZE - 216, SIZE // 2 + 150, SIZE - 176), radius=20, fill=WHITE)
    # столбики
    base, width, gap = SIZE - 380, 112, 52
    left = (SIZE - (3 * width + 2 * gap)) // 2
    for i, h in enumerate((150, 250, 360)):
        x = left + i * (width + gap)
        d.rounded_rectangle((x, base - h, x + width, base), radius=20, fill=DARK if i < 2 else BLUE)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = draw()
    for name, px in {"32x32.png": 32, "128x128.png": 128, "128x128@2x.png": 256, "icon.png": 512}.items():
        img.resize((px, px), Image.Resampling.LANCZOS).save(OUT / name)
    img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Значки записаны в {OUT}")


if __name__ == "__main__":
    main()

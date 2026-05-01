"""
process_logo.py — 将 logo.png 切割转换为各尺寸图标文件。

用法：
    python process_logo.py

输出（均在项目根目录 assets/ 下）：
    assets/icon.ico      — Windows 多分辨率图标（16/24/32/48/64/128/256px）
    assets/icon_32.png   — 系统托盘用（32x32 RGBA）
    assets/icon_64.png   — 窗口/任务栏用（64x64 RGBA）
    assets/icon_256.png  — 高分辨率备用（256x256 RGBA）
"""

from pathlib import Path
from PIL import Image

SRC = Path("logo.png")
OUT = Path("assets")
OUT.mkdir(exist_ok=True)

img = Image.open(SRC).convert("RGBA")
print(f"原始尺寸: {img.size}")


def fit_square(src: Image.Image, size: int) -> Image.Image:
    """等比缩放并居中到透明正方形画布。"""
    tmp = src.copy()
    tmp.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - tmp.width) // 2
    y = (size - tmp.height) // 2
    canvas.paste(tmp, (x, y), tmp)
    return canvas


for size, name in [(32, "icon_32.png"), (64, "icon_64.png"), (256, "icon_256.png")]:
    fit_square(img, size).save(OUT / name)
    print(f"已生成: assets/{name}  ({size}x{size})")

ico_sizes = [16, 24, 32, 48, 64, 128, 256]
img.save(
    OUT / "icon.ico",
    format="ICO",
    sizes=[(s, s) for s in ico_sizes],
)
print(f"已生成: assets/icon.ico  ({'/'.join(str(s) for s in ico_sizes)}px)")
print("\n完成。")

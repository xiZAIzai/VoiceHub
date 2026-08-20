"""生成应用图标 assets/voicehub.ico（蓝底白色声波圆点，与托盘图标同风格）。

用法：python scripts/make_icon.py
产物为多尺寸 ico（16~256），供 PyInstaller 打包 exe 与后续窗口/托盘使用。
"""
from pathlib import Path

from PIL import Image, ImageDraw

# 与 win_backend._make_tray_icon 保持同风格：蓝底 + 白色圆点
BG = (37, 99, 235)
FG = (255, 255, 255)


def _draw(size: int) -> Image.Image:
    """按尺寸画一枚圆角方块图标。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 5  # 圆角半径
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=BG)
    # 中央声波圆点 + 两侧弧线（麦克风/声波意象）
    cx = size / 2
    cy = size / 2
    dot = size * 0.14
    d.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=FG)
    for i, (rx, w) in enumerate([(0.26, 0.035), (0.38, 0.03)]):
        # 两侧弧线用细椭圆环近似（粗细随尺寸缩放）
        d.arc((size * (0.5 - rx * 2), size * (0.5 - rx), size * (0.5 + rx * 2), size * (0.5 + rx)),
              start=200, end=340, fill=FG, width=max(1, int(size * w)))
    return img


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "assets" / "voicehub.ico"
    out.parent.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [_draw(s) for s in sizes]
    imgs[-1].save(out, format="ICO", sizes=[(s, s) for s in sizes],
                  append_images=imgs[:-1])
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()

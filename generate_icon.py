#!/usr/bin/env python3
"""生成 Disk Analyzer 应用图标 (app.ico)"""

from PIL import Image, ImageDraw, ImageFont
import math

SIZES = [16, 32, 48, 64, 128, 256]


def draw_icon(size: int) -> Image.Image:
    """在给定尺寸的画布上绘制图标。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = max(2, size // 10)

    # ── 背景圆角矩形 ──
    r = size // 6
    bg_color = (41, 128, 185)  # 蓝色基调

    # 绘制圆角矩形（用椭圆近似四角 + 中间矩形）
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=r, fill=bg_color
    )

    # ── 磁盘盘片（内部浅色圆） ──
    cx, cy = size // 2, size // 2
    disk_r = size // 4 + size // 16
    inner_color = (52, 152, 219)
    draw.ellipse(
        [cx - disk_r, cy - disk_r, cx + disk_r, cy + disk_r],
        fill=inner_color, outline=(255, 255, 255, 180), width=max(1, size // 40)
    )

    # ── 盘片中心孔 ──
    hole_r = size // 12
    draw.ellipse(
        [cx - hole_r, cy - hole_r, cx + hole_r, cy + hole_r],
        fill=(236, 240, 241)
    )

    # ── 三条柱状条（在盘片上） ──
    bar_w = max(2, size // 18)
    bar_gap = max(1, size // 36)
    bar_base_y = cy + disk_r // 3
    colors = [(46, 204, 113), (241, 196, 15), (231, 76, 60)]

    for i, (bcolor, height_ratio) in enumerate(zip(colors, [0.5, 0.75, 1.0])):
        bar_h = int(disk_r * height_ratio * 0.8)
        bx = cx + (i - 1) * (bar_w + bar_gap)
        draw.rectangle(
            [bx, bar_base_y - bar_h, bx + bar_w, bar_base_y],
            fill=bcolor
        )

    # ── 左上角放大镜（小尺寸不画） ──
    if size >= 48:
        glass_cx = margin + size // 5
        glass_cy = margin + size // 5
        glass_r = size // 10
        handle_w = max(1, size // 20)
        handle_len = size // 8

        # 镜框
        draw.ellipse(
            [glass_cx - glass_r, glass_cy - glass_r,
             glass_cx + glass_r, glass_cy + glass_r],
            outline=(255, 255, 255, 230), width=max(2, size // 32)
        )
        # 手柄（45度角）
        angle = math.radians(45)
        hx = glass_cx + int(glass_r * 0.7)
        hy = glass_cy + int(glass_r * 0.7)
        ex = hx + int(handle_len * math.cos(angle))
        ey = hy + int(handle_len * math.sin(angle))
        draw.line([hx, hy, ex, ey], fill=(255, 255, 255, 230),
                  width=handle_w)

    return img


def main():
    images = []
    for s in SIZES:
        img = draw_icon(s)
        images.append(img)

    output_path = "app.ico"
    images[0].save(
        output_path, format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[1:]
    )
    print(f"Generated {output_path} with sizes: {SIZES}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""对渲染出来的截图做像素级体检（不依赖任何外部视觉 API）。

检查项：
  1. 背景是不是浅色（深色占比应极低）
  2. 文字（暗像素）的外接矩形，以及到左右下边缘的留白 → 判断有没有被裁掉/顶到边
  3. 文字块分布（按行扫描）→ 判断内容是不是挤成一团/大面积空白

用法：python .verify/check_render_margins.py
"""

import os
import sys

from PIL import Image

ART = os.path.join(os.path.expanduser("~"), "Desktop", "AILab", ".dsh-vision-toolkit", "artifacts")
# 自动扫描产物目录里的全部截图，避免新增页面漏检
SHOTS = sorted(f for f in os.listdir(ART) if f.lower().endswith(".png")) if os.path.isdir(ART) else []

THRESHOLD = 140  # 灰度低于此值算「文字/深色」


def analyze(path):
    im = Image.open(path).convert("L")
    w, h = im.size
    mask = im.point(lambda v: 255 if v < THRESHOLD else 0)
    bbox = mask.getbbox()
    dark = sum(mask.histogram()[255:])  # 值为 255 的像素数
    dark_pct = dark / float(w * h) * 100

    # 按行统计暗像素，找出文字行的分布区间
    rows = []
    small = mask.resize((1, h))
    for y in range(h):
        rows.append(small.getpixel((0, y)) > 0)
    bands, start = [], None
    for y, has in enumerate(rows):
        if has and start is None:
            start = y
        elif not has and start is not None:
            if y - start >= 2:
                bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, h))

    return {
        "size": (w, h),
        "dark_pct": round(dark_pct, 3),
        "text_bbox": bbox,
        "margin_left": bbox[0] if bbox else None,
        "margin_top": bbox[1] if bbox else None,
        "margin_right": (w - bbox[2]) if bbox else None,
        "margin_bottom": (h - bbox[3]) if bbox else None,
        "text_bands": len(bands),
        "band_span": (bands[0][0], bands[-1][1]) if bands else None,
    }


def main():
    ok = True
    for name in SHOTS:
        path = os.path.join(ART, name)
        if not os.path.exists(path):
            print(f"MISSING {name}")
            ok = False
            continue
        r = analyze(path)
        w, h = r["size"]
        flags = []
        if r["dark_pct"] > 12:
            flags.append("深色占比偏高，可能不是浅色主题")
        if r["text_bbox"] is None:
            flags.append("找不到任何文字像素")
        else:
            if r["margin_left"] < 4 or r["margin_right"] < 4:
                flags.append("文字顶到左右边缘，可能被裁")
            if r["margin_bottom"] < 2:
                flags.append("文字顶到底边，可能被裁")
        if r["text_bands"] < 3:
            flags.append("文字行太少，内容可能没渲染出来")
        status = "OK  " if not flags else "WARN"
        if flags:
            ok = False
        print(f"[{status}] {name}  {w}x{h}")
        print(f"        背景深色占比 {r['dark_pct']}% | 文字行 {r['text_bands']} 段 "
              f"| 文字范围 y={r['band_span']}")
        print(f"        留白 左{r['margin_left']} 上{r['margin_top']} "
              f"右{r['margin_right']} 下{r['margin_bottom']}")
        for f in flags:
            print(f"        ⚠ {f}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

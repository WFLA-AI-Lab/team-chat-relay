#!/usr/bin/env python3
"""像素级体检：把截图当真，量"肉眼能看见的东西"到底画出来了没有。

为什么需要它：DOM 探针只能证明"元素有宽度"，证明不了"颜色真画上去了"。
本脚本读 PNG 原始像素，专门抓这类"CSS 写错了、页面看着正常其实整块是空的"问题：

  1) 数据看板条形图：必须能找到 >=15 条横向蓝条，每条高 8~12px、起点对齐同一列
     （回归目标：.bar-track/.bar-fill 是 span，漏写 display:block 时宽度全为 0，
      截图里只剩背景色——这条检查当场就能抓住）
  2) 作品页聚焦锚点：必须只有一张作品卡被高亮（圆角描边成对出现：上边 + 下边）

用法：
    python tests/render_ops_pages.py --shot          # 先生成截图
    python tests/render_share_landing.py --shot
    python tests/check_pixels.py                     # 再量像素

没有截图 / 没装 Pillow / 没浏览器 -> SKIP（不算失败，也不会假绿）。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SHOT_DIR = os.path.join(ROOT, ".verify", "render")

# 条形图的蓝色渐变：#2967ff -> #0fb5c9，两端的蓝都远高于红
BARS_PNG = os.path.join(SHOT_DIR, "shot-admin-stats.png")
FOCUS_PNGS = [
    os.path.join(SHOT_DIR, "showcase-anchor.png"),
    os.path.join(SHOT_DIR, "shot-showcase-anchor.png"),
]

results = []


def record(name, ok, detail):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name)
    if detail:
        print("      " + detail)


def is_bar_blue(pixel):
    """条形图渐变的判据：蓝通道明显压过红通道。"""
    r, g, b = pixel[:3]
    return b > 140 and (b - r) > 40


def is_focus_blue(pixel):
    """聚焦描边 var(--blue)=#2967ff 的判据（比渐变更严，避免和正文混）。"""
    r, g, b = pixel[:3]
    return b > 200 and r < 110 and g < 150


def load(name):
    try:
        from PIL import Image
    except ImportError:
        return None, "没装 Pillow（pip install pillow）"
    if not os.path.exists(name):
        return None, "截图不存在：" + os.path.basename(name)
    return Image.open(name).convert("RGB"), ""


def region_runs(img, x_from, y_from, y_to, pred, min_run):
    """逐行找 >=min_run 的连续命中段，返回 [(y, 宽度, 起点x)]。"""
    width, height = img.size
    px = img.load()
    rows = []
    for y in range(max(0, y_from), min(height, y_to)):
        run = 0
        best = 0
        start = 0
        best_start = 0
        for x in range(x_from, width):
            if pred(px[x, y]):
                if run == 0:
                    start = x
                run += 1
                if run > best:
                    best = run
                    best_start = start
            else:
                run = 0
        if best >= min_run:
            rows.append((y, best, best_start))
    return rows


def group_bands(rows, gap=3):
    """把相邻行归成一条"条"（条形图每根 10px 高）。"""
    if not rows:
        return []
    bands = []
    current = [rows[0]]
    for row in rows[1:]:
        if row[0] - current[-1][0] > gap:
            bands.append(current)
            current = []
        current.append(row)
    bands.append(current)
    return bands


def check_bars():
    img, err = load(BARS_PNG)
    if img is None:
        record("数据看板条形图", None, err)
        return
    # 跳过页头（导航/公告也是蓝的），只看正文区
    rows = region_runs(img, 200, 120, img.size[1], is_bar_blue, 30)
    bands = [b for b in group_bands(rows) if 8 <= (b[-1][0] - b[0][0] + 1) <= 12]
    detail = "找到 %d 条蓝条" % len(bands)
    if bands:
        widths = [max(r[1] for r in b) for b in bands]
        starts = sorted({min(r[2] for r in b) for b in bands})
        detail += "，宽度 %s，起点列 %s" % (widths, starts)
    ok = len(bands) >= 15
    if not ok:
        detail += "（期望 >=15：条形图没画出来，检查 .bar-track/.bar-fill 是否 display:block）"
    record("数据看板条形图", ok, detail)


def check_focus_ring():
    path = next((p for p in FOCUS_PNGS if os.path.exists(p)), None)
    if path is None:
        record("作品卡聚焦高亮", None, "截图不存在（先跑 render_share_landing.py --shot）")
        return
    img, err = load(path)
    if img is None:
        record("作品卡聚焦高亮", None, err)
        return
    rows = region_runs(img, 0, 0, img.size[1], is_focus_blue, 60)
    bands = group_bands(rows)
    # 一张卡的描边 = "横跨整张卡"的上边一条 + 下边一条；
    # 卡里的蓝色按钮/链接文字是窄条（实测 ~64-72px），靠"够不够宽"就能区分开。
    wide = [b for b in bands if max(r[1] for r in b) >= 250]
    detail = "蓝色横条共 %d 条，其中跨卡宽 %d 条" % (len(bands), len(wide))
    if len(wide) == 2:
        top = wide[0][0][0]
        bottom = wide[-1][-1][0]
        width = max(r[1] for r in wide[0])
        ok = 100 <= (bottom - top) <= 700 and width >= 250
        detail += "（上边 y=%d、下边 y=%d、宽 %dpx -> 卡片高 %dpx）" % (
            top, bottom, width, bottom - top)
        if not ok:
            detail += " 两条跨卡宽横线不像一张卡的描边"
    else:
        ok = False
        detail += "（期望恰好 2 条：不足 = 高亮没画出来，超出 = 不止一张卡被高亮）"
    record("作品卡聚焦高亮", ok, detail)


def main():
    print("== 像素级体检（读 .verify/render 下的截图）==")
    if not os.path.isdir(SHOT_DIR):
        print("SKIP 没有 .verify/render 目录，先跑 render_*_pages.py --shot")
        return 0
    check_bars()
    check_focus_ring()

    skipped = [n for n, ok, _ in results if ok is None]
    failed = [n for n, ok, _ in results if ok is False]
    passed = [n for n, ok, _ in results if ok is True]
    print("\n%d/%d 项通过，%d 项跳过" % (len(passed), len(results), len(skipped)))
    for name in failed:
        print("  FAIL " + name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

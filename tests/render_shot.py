#!/usr/bin/env python3
"""共享截图工具：用本机 Chrome/Edge 给渲染出来的页面拍照（不联网、不走任何 API）。

为什么要它：光看 HTML 源码看不出"条形图整条看不见""公告横幅空着"这类问题，
必须真渲染出来量像素。本模块只负责"启动浏览器 + 出 PNG"，判断交给调用方
（tests/check_pixels.py / check_rendered_layout.py）——这样同一张图能反复复查。

用法：
    from render_shot import shot, find_browser

    shot(".verify/render/admin-stats.html")        # -> .verify/render/shot-admin-stats.png
    shot(url_with_hash, out_png, size=(1280, 900))

找不到浏览器时返回 None（调用方自己决定 SKIP 还是 FAIL，别在这里抛异常）。
"""

import os
import subprocess

# 本机可能装的是 Chrome 或 Edge（Chromium 内核，命令行参数通用）
BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_browser():
    """返回可用的 Chromium 内核浏览器路径，找不到返回 None。"""
    for path in BROWSER_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def file_url(path):
    """本地路径 -> file:/// URL（Windows 盘符要转成正斜杠）。"""
    return "file:///" + os.path.abspath(path).replace("\\", "/").lstrip("/")


def shot(target, out_png=None, size=(1280, 1500), budget=6000, label=""):
    """给 target（本地 HTML 路径、带 #anchor 的 URL 都行）拍一张 PNG。

    size   视口大小，页面长的用 (1280, 1500)，一屏内的用 (1280, 900)
    budget 虚拟时间预算（毫秒）：页面有 JS 抓数据/加 class 时要留够
    """
    browser = find_browser()
    if not browser:
        print("没找到 Chrome/Edge，跳过截图")
        return None

    pure = target.split("#")[0]
    url = target if target.startswith(("file:", "http://", "https://")) else file_url(target)
    if out_png is None:
        stem = os.path.splitext(os.path.basename(pure))[0]
        out_png = os.path.join(os.path.dirname(os.path.abspath(pure)), "shot-%s.png" % stem)

    cmd = [
        browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--window-size=%d,%d" % size,
        "--virtual-time-budget=%d" % budget,
        "--screenshot=%s" % out_png,
        url,
    ]
    subprocess.run(cmd, check=False, capture_output=True)
    ok = os.path.exists(out_png) and os.path.getsize(out_png) > 0
    if label:
        print("screenshot [%s]: %s %s" % (label, out_png, ok))
    else:
        print("screenshot:", out_png, ok)
    return out_png if ok else None

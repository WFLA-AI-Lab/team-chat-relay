#!/usr/bin/env python3
"""渲染页面的"布局体检"：用无头 Chrome 真跑一遍，把能量化的视觉问题查出来。

为什么需要它：截图只能靠人眼看，而有些问题（横向溢出、条形图宽度没生效、
公告横幅其实是空的、复制/分享按钮没渲染出来、锚点高亮没加上）是**可以量化**的。
这个脚本在渲染好的 HTML 末尾注入一段探针脚本，让浏览器自己量，再把结果读回来。

查出这些：
  - overflowX      整页横向溢出像素（>0 说明有元素撑破屏幕，手机上会很难看）
  - overflowEls    撑破屏幕的前几个元素
  - bars           每个 .bar-fill 的实际像素宽度（0 = 宽度没生效/值为 0）
  - notice         公告横幅是否存在 + 头几个字（防止渲染成空橙条）
  - share/copy     分享按钮、复制到我的按钮各几个
  - focusCard      带 .focus 的卡片 id，以及它是否真的进了视口

用法：
    python tests/check_rendered_layout.py                 # 体检 .verify/render/*.html
    python tests/check_rendered_layout.py --html a.html   # 只查指定文件
依赖本机 Chrome（找不到就 SKIP，不算失败）。
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
RENDER_DIR = os.path.join(ROOT, ".verify", "render")
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

PROBE = r"""
<script>
(function () {
  function q(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }
  var de = document.documentElement;
  var out = {
    overflowX: de.scrollWidth - de.clientWidth,
    viewportW: de.clientWidth
  };
  out.overflowEls = q("body *").filter(function (e) {
    var r = e.getBoundingClientRect();
    return r.width > 0 && (r.right > de.clientWidth + 1 || r.left < -1);
  }).slice(0, 5).map(function (e) { return e.tagName + "." + (e.className || "").toString().split(" ")[0]; });
  out.bars = q(".bar-fill").map(function (b) {
    return { w: Math.round(b.getBoundingClientRect().width), v: (b.textContent || "").trim() };
  });
  var n = document.querySelector(".notice-banner, .notice-bar");
  out.notice = n ? (n.textContent || "").trim().slice(0, 30) : null;
  out.share = q(".js-share").length;
  out.copy = q("form").filter(function (f) {
    return f.getAttribute("action") && f.getAttribute("action").indexOf("/copy") >= 0;
  }).length;
  var f = document.querySelector(".pcard.focus");
  out.focusCard = f ? f.id : null;
  if (f) {
    var r = f.getBoundingClientRect();
    out.focusVisible = r.top < window.innerHeight && r.bottom > 0;
    out.focusTop = Math.round(r.top);
  }
  out.cards = q(".pcard").length;
  var d = document.createElement("div");
  d.id = "layout-probe";
  d.textContent = JSON.stringify(out);
  document.body.appendChild(d);
})();
</script>
"""


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def probe(chrome, path, url_hash=""):
    with open(path, "r", encoding="utf-8") as fh:
        html = fh.read()
    tmp = path + ".probe.html"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(html.replace("</body>", PROBE + "</body>", 1))
    url = "file:///" + tmp.replace("\\", "/").lstrip("/") + url_hash
    proc = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--virtual-time-budget=3000",
         "--window-size=1280,900", "--dump-dom", url],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    os.remove(tmp)
    m = re.search(r'<div id="layout-probe">([^<]*)</div>', proc.stdout or "")
    if not m:
        return None
    return json.loads(m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", action="append", default=[],
                    help="只体检指定 HTML（可重复）；缺省体检 .verify/render/*.html")
    ap.add_argument("--hash", default="",
                    help="打开页面时附加的 hash（测分享直达时用）")
    args = ap.parse_args()

    chrome = find_chrome()
    if not chrome:
        print("SKIP 本机没装 Chrome/Edge，跳过布局体检")
        return 0
    files = args.html or sorted(glob.glob(os.path.join(RENDER_DIR, "*.html")))
    files = [os.path.abspath(f) for f in files if not f.endswith(".probe.html")]
    if not files:
        print("SKIP 没有可体检的 HTML（先跑 tests/render_ops_pages.py）")
        return 0

    bad = 0
    for path in files:
        name = os.path.basename(path)
        # 分享直达页面必须带 hash 打开，否则测的不是"直达"
        fhash = args.hash
        if "showcase" in name and not fhash:
            fhash = "#pc-WFLA-AI-Lab-team-chat-relay"
        data = probe(chrome, path, fhash)
        if data is None:
            print(f"FAIL {name}: 探针没执行（页面可能脚本报错）")
            bad += 1
            continue
        issues = []
        if data["overflowX"] > 1:
            issues.append(f"横向溢出 {data['overflowX']}px {data['overflowEls']}")
        zero_bars = [b for b in data["bars"] if b["w"] == 0]
        if data["bars"] and zero_bars:
            issues.append(f"{len(zero_bars)} 个条形图宽度为 0")
        if name.startswith("showcase") and data["focusCard"] is None:
            issues.append("分享直达没高亮任何卡片")
        if data["notice"] == "":
            issues.append("公告横幅渲染成了空条")
        if "projects-share" in name and data["notice"] is None:
            issues.append("公告横幅没出现")
        if "projects-no-notice" in name and data["notice"] is not None:
            issues.append("公告已清空但横幅还在")
        if "projects-share" in name and data["share"] < 2:
            issues.append(f"分享按钮只有 {data['share']} 个（应有 2 个）")

        print(("FAIL " if issues else "PASS ") + name)
        print("      " + json.dumps(data, ensure_ascii=False))
        for i in issues:
            print("      ! " + i)
        bad += 1 if issues else 0
    print(f"\n{len(files) - bad}/{len(files)} 个页面布局体检通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

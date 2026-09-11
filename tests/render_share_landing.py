#!/usr/bin/env python3
"""作品分享"链接直达"落地页本地复核工具。

社团网站 showcase.html 是按 hash 落地高亮的：作品页复制出来的分享文案里带
    https://wfla-ailab.top/showcase.html#pc-<owner>-<repo>
打开后该卡片应该被蓝框高亮并滚到屏幕中间。

问题：showcase.html 的数据来自线上接口，本地直接打开是空的，没法复核。
做法：**不改页面逻辑**，只在页面里注入一小段 fetch 拦截（把两个线上接口换成假数据），
生成 .verify/render/showcase-harness.html，然后用无头浏览器带 hash 打开截图。

用法：
    python tests/render_share_landing.py            # 生成 harness + 打印打开方式
    python tests/render_share_landing.py --shot     # 直接用本机 Chrome 截图（默认有 hash）
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SHOWCASE = os.path.normpath(os.path.join(ROOT, "..", "Website", "showcase.html"))
OUT_DIR = os.path.join(ROOT, ".verify", "render")
OUT_HTML = os.path.join(OUT_DIR, "showcase-harness.html")

# 假数据：两条作品、两个作者，其中目标卡片放最后，才看得出"自动滚到它"
TARGET_OWNER, TARGET_REPO = "WFLA-AI-Lab", "team-chat-relay"

FAKE = """
<script>
(function () {
  var items = [
    { display_name: "运动会计时器", tag: "工具", description: "给体育组做的计时小工具",
      github_owner: "someone-else", github_repo: "sports-timer", html_url: "", demo_url: "" },
    { display_name: "校园植物图鉴", tag: "学习", description: "拍照认植物",
      github_owner: "green-club", github_repo: "plant-book", html_url: "", demo_url: "" },
    { display_name: "文章助手", tag: "学习", description: "社团主项目：帮同学改作文",
      github_owner: "%(owner)s", github_repo: "%(repo)s", html_url: "", demo_url: "" }
  ];
  var real = window.fetch;
  window.fetch = function (url) {
    var u = String(url);
    if (u.indexOf("projects.json") >= 0) {
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(items); } });
    }
    if (u.indexOf("api.github.com") >= 0) {
      return Promise.resolve({ ok: true, status: 200, json: function () {
        return Promise.resolve({ language: "Python", stargazers_count: 12, updated_at: "2026-09-13T00:00:00Z" });
      } });
    }
    return real.apply(this, arguments);
  };
})();
</script>
""" % {"owner": TARGET_OWNER, "repo": TARGET_REPO}


def build():
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(SHOWCASE, "r", encoding="utf-8") as fh:
        html = fh.read()
    anchor = '<script>\n(function () {'
    assert anchor in html, "showcase.html 结构变了，注入点找不到（请更新本脚本）"
    html = html.replace(anchor, FAKE + "\n" + anchor, 1)
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    url = "file:///" + OUT_HTML.replace("\\", "/").lstrip("/") \
        + f"#pc-{TARGET_OWNER}-{TARGET_REPO}"
    print("wrote", OUT_HTML, os.path.getsize(OUT_HTML), "bytes")
    print("打开：", url)
    return url


def shot(url, out_png=None):
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(chrome):
        print("没找到 Chrome，跳过截图：", chrome)
        return None
    out_png = out_png or os.path.join(OUT_DIR, "showcase-anchor.png")
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--window-size=1280,900", "--virtual-time-budget=6000",
           f"--screenshot={out_png}", url]
    subprocess.run(cmd, check=False, capture_output=True)
    print("screenshot:", out_png, os.path.exists(out_png))
    return out_png


if __name__ == "__main__":
    u = build()
    if "--shot" in sys.argv:
        shot(u)

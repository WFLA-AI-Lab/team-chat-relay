#!/usr/bin/env python3
"""chat.wfla-ailab.top/portal —— AI 社团站点门户。

显眼的跳转页，串联三个站点：
- 聊天站：同域名的 Open WebUI 根路径（href="/"）；
- Agent 擂台：同域名 /arena 子路径（登录 cookie 通用）；
- 教程站：独立子域名 TUTORIAL_DOMAIN（默认 tutorial.wfla-ailab.top）。

依赖：flask（容器启动时自动安装）。
"""

import os

from flask import Flask, render_template

app = Flask(__name__)


@app.route("/")
def index():
    """门户首页：三张大卡片，各指向一个站点。"""
    tutorial_domain = os.environ.get("TUTORIAL_DOMAIN", "tutorial.wfla-ailab.top")
    return render_template("index.html", tutorial_url="https://" + tutorial_domain)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8083)

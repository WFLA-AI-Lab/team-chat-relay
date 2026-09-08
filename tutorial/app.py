#!/usr/bin/env python3
"""tutorial.wfla-ailab.top —— 教程站。

把 tutorial/content/*.md 渲染成网页。管理员只需编辑 markdown 文件，
刷新即生效；文件可进 git 版本管理。

依赖：flask, markdown（容器启动时自动安装）。
"""

import os
import re

import markdown
import urllib.request
from flask import Flask, abort, render_template, request

BASE = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(BASE, "content")

CHAT_URL = "https://" + os.environ.get("CHAT_DOMAIN", "chat.wfla-ailab.top").rstrip("/")

app = Flask(__name__)


@app.context_processor
def inject_site_links():
    """给模板注入聊天站地址与登录态（登录按钮/用户名显示用）。"""
    return {"chat_url": CHAT_URL, "username": current_username()}


def current_username():
    """读聊天站 token cookie 并向 Open WebUI 验证，返回用户名或 None。

    注意：教程站与聊天站不同子域，浏览器默认不会带 chat 域的 cookie，
    所以这里大多数时候返回 None（显示登录按钮）；登录后从聊天站「打开教程站」
    或配置 Cookie Domain 时才能自动识别。作为兜底，前端始终提供显眼登录入口。
    """
    token = request.cookies.get("token")
    if not token:
        return None
    try:
        req = urllib.request.Request(
            f"{os.environ.get('OPENWEBUI_URL', 'http://open-webui:8080').rstrip('/')}/api/v1/auths/",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            import json as _json
            info = _json.loads(resp.read().decode("utf-8"))
            return info.get("name") or info.get("email")
    except Exception:
        return None


def read_doc(fn):
    """读取 md 文件：解析 frontmatter title / level，返回 (title, level, body)。"""
    path = os.path.join(CONTENT, fn)
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    title = fn[:-3]
    level = "入门"
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
    if m:
        fm = m.group(1)
        tm = re.search(r"title:\s*(.+)", fm)
        if tm:
            title = tm.group(1).strip().strip('"').strip("'")
        lm = re.search(r"level:\s*(.+)", fm)
        if lm:
            level = lm.group(1).strip().strip('"').strip("'")
        raw = raw[m.end():]
    return title, level, raw


@app.route("/")
def index():
    docs = []
    for fn in sorted(os.listdir(CONTENT)):
        if fn.endswith(".md") and not fn.startswith("_"):
            title, level, _ = read_doc(fn)
            docs.append({"slug": fn[:-3], "title": title, "level": level})
    return render_template("index.html", docs=docs)


@app.route("/<slug>")
def article(slug):
    safe = re.sub(r"[^0-9a-zA-Z_-]", "", slug)
    fn = safe + ".md"
    if not os.path.exists(os.path.join(CONTENT, fn)):
        abort(404)
    title, level, body = read_doc(fn)
    # 分层访问：入门篇对所有人公开；进阶/维护篇需要登录（复用聊天站账号）。
    user = current_username()
    if level != "入门" and not user:
        return render_template("gate.html", title=title, level=level), 200
    html = markdown.markdown(body, extensions=["fenced_code", "tables", "sane_lists"])
    return render_template("article.html", title=title, content=html, level=level)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082)

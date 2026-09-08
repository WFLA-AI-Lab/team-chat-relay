#!/usr/bin/env python3
"""tutorial.wfla-ailab.top —— 教程站。

把 tutorial/content/*.md 渲染成网页。管理员只需编辑 markdown 文件，
刷新即生效；文件可进 git 版本管理。

依赖：flask, markdown（容器启动时自动安装）。
"""

import os
import re

import markdown
from flask import Flask, abort, render_template

BASE = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(BASE, "content")

app = Flask(__name__)


@app.context_processor
def inject_site_links():
    """给模板注入聊天站地址（导航条「门户/聊天站/擂台」链接用）。"""
    return {"chat_url": "https://" + os.environ.get("CHAT_DOMAIN", "chat.wfla-ailab.top")}


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
    html = markdown.markdown(body, extensions=["fenced_code", "tables", "sane_lists"])
    return render_template("article.html", title=title, content=html, level=level)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082)

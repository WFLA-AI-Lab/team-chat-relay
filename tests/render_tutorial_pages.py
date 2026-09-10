#!/usr/bin/env python3
"""把教程站的关键页面渲染成静态 HTML，供人工/截图复核。

用法：python tests/render_tutorial_pages.py
产出：.verify/render/tutorial-*.html

用的是真实路由与模板，只有聊天站（OpenWebUI）是打桩的。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
TUTORIAL_DIR = os.path.join(ROOT, "tutorial")
OUT_DIR = os.path.join(ROOT, ".verify", "render")
sys.path.insert(0, TUTORIAL_DIR)

os.environ.setdefault("CHAT_DOMAIN", "chat.wfla-ailab.top")
os.environ.setdefault("OPENWEBUI_URL", "http://stub-openwebui:8080")
os.environ.setdefault("COOKIE_DOMAIN", "")

import app as tut  # noqa: E402

TOKEN = "tok-render"
tut.owu_signin = lambda email, password: TOKEN if email and password else None


def render():
    os.makedirs(OUT_DIR, exist_ok=True)
    written = []

    def dump(name, html):
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        written.append(path)

    original = tut.current_username
    try:
        # 1) 未登录：登录页
        tut.current_username = lambda: None
        with tut.app.test_client() as c:
            dump("tutorial-login.html", c.get("/login").get_data(as_text=True))
            dump("tutorial-gate.html", c.get("/07-glossary").get_data(as_text=True))
            dump("tutorial-index.html", c.get("/").get_data(as_text=True))

        # 2) 已登录：进阶篇正文
        tut.current_username = lambda: "张三"
        with tut.app.test_client() as c:
            dump("tutorial-article.html", c.get("/07-glossary").get_data(as_text=True))

        # 3) 登录失败提示（打桩成"聊天站说密码不对"）
        tut.current_username = lambda: None
        good = tut.owu_signin
        tut.owu_signin = lambda email, password: None
        try:
            with tut.app.test_client() as c:
                dump("tutorial-login-error.html",
                     c.post("/login", data={"email": "a@b.c", "password": "x"}).get_data(as_text=True))
        finally:
            tut.owu_signin = good
    finally:
        tut.current_username = original

    for p in written:
        print("wrote", p, os.path.getsize(p), "bytes")
    return written


if __name__ == "__main__":
    render()

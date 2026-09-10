#!/usr/bin/env python3
"""tutorial.wfla-ailab.top —— 教程站。

把 tutorial/content/*.md 渲染成网页。管理员只需编辑 markdown 文件，
刷新即生效；文件可进 git 版本管理。

依赖：flask, markdown（容器启动时自动安装）。
"""

import json
import os
import re

import markdown
import urllib.request
from flask import Flask, abort, make_response, redirect, render_template, request

BASE = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(BASE, "content")

CHAT_URL = "https://" + os.environ.get("CHAT_DOMAIN", "chat.wfla-ailab.top").rstrip("/")
OPENWEBUI_URL = os.environ.get("OPENWEBUI_URL", "http://open-webui:8080").rstrip("/")
# 让登录 cookie 跨子域名生效；留空则按请求域名自动判断（*.wfla-ailab.top）
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", "").strip()
COOKIE_MAX_AGE = int(os.environ.get("LOGIN_COOKIE_MAX_AGE", str(30 * 24 * 3600)))

app = Flask(__name__)


@app.context_processor
def inject_site_links():
    """给模板注入聊天站地址与登录态（登录按钮/用户名显示用）。"""
    return {"chat_url": CHAT_URL, "username": current_username()}


def current_username():
    """读 token cookie 并向 Open WebUI 验证，返回用户名或 None。

    教程站与聊天站不同子域，浏览器不会自动带上聊天站的 host-only cookie，
    所以本站提供 /login：用聊天站账号在本站登录一次，把 token 种到
    .wfla-ailab.top 上（见 login()），教程站之后就能自己认出登录态。
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


def owu_signin(email, password):
    """拿聊天站账号换一个 token；失败返回 None。

    密码只用于这一次校验，不落库、不写日志（与擂台 school_auth 的约定一致）。
    """
    payload = json.dumps({"email": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{OPENWEBUI_URL}/api/v1/auths/signin",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        token = body.get("token") if isinstance(body, dict) else None
        return token or None
    except Exception:
        return None


def cookie_domain():
    """登录 cookie 的作用域：默认跟随请求域名，让 chat./arena. 共用一个登录态。"""
    if COOKIE_DOMAIN:
        return COOKIE_DOMAIN
    host = (request.host or "").split(":")[0]
    if host == "wfla-ailab.top" or host.endswith(".wfla-ailab.top"):
        return ".wfla-ailab.top"
    return None


def safe_next(target):
    """只允许跳回本站路径，防止被当成开放重定向跳板。"""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


@app.route("/login", methods=["GET", "POST"])
def login():
    """教程站站内登录：用聊天站账号在本站登录，登录完回原来那篇教程。

    成功后把 token 种到 .wfla-ailab.top，于是：
      - 教程站的进阶/维护篇立刻可读；
      - 同一浏览器访问聊天站/擂台也认这个登录态。
    """
    nxt = safe_next(request.values.get("next", "/"))
    if request.method != "POST":
        return render_template("login.html", error="", email="", next=nxt)

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    token = owu_signin(email, password) if email and password else None
    if not token:
        return render_template(
            "login.html", next=nxt, email=email,
            error="邮箱或密码不对（请用聊天站的账号）",
        ), 200

    resp = make_response(redirect(nxt))
    # 不设 httponly：聊天站前端同样要读这个 cookie，保持一致
    resp.set_cookie(
        "token", token, max_age=COOKIE_MAX_AGE, domain=cookie_domain(),
        path="/", samesite="Lax", secure=bool(request.is_secure), httponly=False,
    )
    return resp


@app.route("/logout")
def logout():
    """退出登录：把跨域 token cookie 清掉。"""
    resp = make_response(redirect("/"))
    resp.delete_cookie("token", domain=cookie_domain(), path="/")
    return resp


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

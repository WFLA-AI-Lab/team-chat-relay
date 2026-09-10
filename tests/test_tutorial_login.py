#!/usr/bin/env python3
"""教程站站内登录自测（不联网、不碰生产）。

验证的是真正上线的那段逻辑：/login 用聊天站账号换 token → 把 token 种到
.wfla-ailab.top → 进阶/维护篇立刻可读。聊天站（OpenWebUI）是打桩的。

用法：python tests/test_tutorial_login.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
TUTORIAL_DIR = os.path.join(ROOT, "tutorial")
sys.path.insert(0, TUTORIAL_DIR)

os.environ.setdefault("CHAT_DOMAIN", "chat.wfla-ailab.top")
os.environ.setdefault("OPENWEBUI_URL", "http://stub-openwebui:8080")
os.environ.setdefault("COOKIE_DOMAIN", "")      # 空 = 按请求域名自动判断

import app as tut  # noqa: E402  （tutorial/app.py）

GOOD = ("member@example.com", "right-pass")
TOKEN = "tok-member-123"
GATED = "/07-glossary"        # level: 进阶 → 需登录
PUBLIC = "/00-ai-agent"       # 无 level → 公开

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def fake_signin(email, password):
    return TOKEN if (email, password) == GOOD else None


tut.owu_signin = fake_signin


def set_logged_in(name=None):
    """把 current_username 换成打桩版本，返回恢复函数。"""
    original = tut.current_username
    tut.current_username = lambda: name
    return lambda: setattr(tut, "current_username", original)


def cookies_of(resp):
    return " | ".join(resp.headers.getlist("Set-Cookie"))


# --------------------------------------------------------------------------
@check("safe_next：只放行站内路径，挡住开放重定向")
def t_safe_next():
    assert tut.safe_next("/07-glossary") == "/07-glossary"
    assert tut.safe_next("/") == "/"
    for bad in ("https://evil.com", "//evil.com", "http://x", "", None, "javascript:alert(1)"):
        assert tut.safe_next(bad) == "/", f"{bad!r} 应该被挡回 /"


@check("GET /login 能打开，含邮箱/密码表单")
def t_login_page():
    with tut.app.test_client() as c:
        r = c.get("/login")
        body = r.get_data(as_text=True)
        assert r.status_code == 200, r.status_code
        assert "登录教程站" in body
        assert 'name="email"' in body and 'name="password"' in body


@check("密码错 → 提示错误、且不下发 token cookie")
def t_bad_password():
    with tut.app.test_client() as c:
        r = c.post("/login", data={"email": GOOD[0], "password": "wrong"},
                   base_url="https://tutorial.wfla-ailab.top")
        body = r.get_data(as_text=True)
        assert r.status_code == 200, r.status_code
        assert "邮箱或密码不对" in body, body[:300]
        assert "token=" not in cookies_of(r), "密码错了不该种 cookie"


@check("登录成功 → 302 回原来那篇 + 种跨子域 token cookie")
def t_login_ok():
    with tut.app.test_client() as c:
        r = c.post("/login",
                   data={"email": GOOD[0], "password": GOOD[1], "next": GATED},
                   base_url="https://tutorial.wfla-ailab.top")
        assert r.status_code == 302, r.status_code
        assert r.headers["Location"].endswith(GATED), r.headers["Location"]
        blob = cookies_of(r)
        assert f"token={TOKEN}" in blob, blob
        # Werkzeug 会去掉前导点，RFC 6265 下浏览器仍视作覆盖所有子域名
        assert re.search(r"Domain=\.?wfla-ailab\.top", blob), blob
        assert "Max-Age=" in blob or "Expires=" in blob, "应是有有效期的持久 cookie"


@check("next 是外站 → 登录后只跳回站内（防钓鱼跳板）")
def t_open_redirect_blocked():
    with tut.app.test_client() as c:
        r = c.post("/login",
                   data={"email": GOOD[0], "password": GOOD[1], "next": "https://evil.com/steal"},
                   base_url="https://tutorial.wfla-ailab.top")
        assert r.status_code == 302, r.status_code
        assert r.headers["Location"].rstrip("/") == "", r.headers["Location"]
        assert "evil.com" not in r.headers["Location"], "不能跳到外站"


@check("非本站域名登录 → 不设置 Domain 属性（cookie 只在本域生效）")
def t_no_domain_for_other_host():
    with tut.app.test_client() as c:
        r = c.post("/login", data={"email": GOOD[0], "password": GOOD[1]},
                   base_url="http://localhost:8082")
        blob = cookies_of(r)
        assert f"token={TOKEN}" in blob, blob
        assert "Domain=" not in blob, f"本地调试不该种跨域 cookie：{blob}"


@check("未登录读进阶篇 → 拦门页；入门篇仍公开")
def t_gate():
    with tut.app.test_client() as c:
        gated = c.get(GATED)
        assert gated.status_code == 200
        assert "需要登录" in gated.get_data(as_text=True), "进阶篇应显示拦门页"

        public = c.get(PUBLIC)
        assert public.status_code == 200
        assert "需要登录后阅读" not in public.get_data(as_text=True), "入门篇不该被拦"


@check("登录后读进阶篇 → 正文出现，导航显示用户名与退出")
def t_gated_readable_when_logged_in():
    restore = set_logged_in("张三")
    try:
        with tut.app.test_client() as c:
            r = c.get(GATED)
            body = r.get_data(as_text=True)
            assert r.status_code == 200, r.status_code
            assert "需要登录后阅读" not in body, "登录后不该再拦"
            assert "张三" in body, "导航应显示用户名"
            assert "/logout" in body, "登录后应出现退出入口"
    finally:
        restore()


@check("拦门页的登录按钮指向本站 /login 并带回跳地址")
def t_gate_links_to_local_login():
    with tut.app.test_client() as c:
        body = c.get(GATED).get_data(as_text=True)
        assert 'href="/login?next=' in body.replace("&#34;", '"'), body[:400]
        assert "chat.wfla-ailab.top/?login=1" not in body, "不该再把人甩到聊天站登录页"


@check("/logout 清掉 token cookie")
def t_logout():
    with tut.app.test_client() as c:
        r = c.get("/logout", base_url="https://tutorial.wfla-ailab.top")
        assert r.status_code == 302, r.status_code
        blob = cookies_of(r)
        assert "token=" in blob, blob
        assert re.search(r"token=;|token=\"\"|Expires=Thu, 01 Jan 1970", blob), blob


@check("聊天站不在线时也不 500，只提示密码错")
def t_openwebui_down():
    original = tut.owu_signin

    def boom(email, password):
        return None          # 模拟聊天站不可达
    tut.owu_signin = boom
    try:
        with tut.app.test_client() as c:
            r = c.post("/login", data={"email": GOOD[0], "password": GOOD[1]})
            assert r.status_code == 200, r.status_code
            assert "邮箱或密码不对" in r.get_data(as_text=True)
    finally:
        tut.owu_signin = original


@check("空邮箱/空密码不发请求（直接拦下）")
def t_empty_input():
    calls = []
    original = tut.owu_signin
    tut.owu_signin = lambda e, p: calls.append((e, p)) or None
    try:
        with tut.app.test_client() as c:
            r = c.post("/login", data={"email": "", "password": ""})
            assert r.status_code == 200, r.status_code
            assert calls == [], f"空输入不该调聊天站：{calls}"
    finally:
        tut.owu_signin = original


def main():
    failed = 0
    for name, fn in CHECKS:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}\n        {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}\n        {type(exc).__name__}: {exc}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

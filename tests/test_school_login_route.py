#!/usr/bin/env python3
"""school-login 路由联调自测：跑真实 Flask 路由 + 假学校系统 + 假聊天站。

不联网、不连生产库、不碰真实学校系统。验证的是真正上线的那段逻辑：
    学校密码校验 → 白名单拦截 → 开通聊天站内部账号 → 建立会话 / 种 cookie

用法：python tests/test_school_login_route.py
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ARENA_DIR = os.path.normpath(os.path.join(HERE, "..", "arena"))
sys.path.insert(0, ARENA_DIR)


# --------------------------------------------------------------------------
# 假学校系统
# --------------------------------------------------------------------------
class SchoolHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        code = (form.get("code") or [""])[0]
        password = (form.get("password") or [""])[0]

        if code in ("2025001", "2024002") and password == "school-pass":
            body = {"ResultType": 0, "Message": "", "Data": {"Url": "/"}}
        elif code in ("2025001", "2025111", "2025112"):
            body = {"ResultType": 1, "Message": "Password error", "Data": None}
        else:
            body = {"ResultType": 1, "Message": "Login account does not exist", "Data": None}

        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


srv = ThreadingHTTPServer(("127.0.0.1", 0), SchoolHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# --------------------------------------------------------------------------
# 环境准备好之后再 import 被测应用
# --------------------------------------------------------------------------
TMP = tempfile.mkdtemp()
DB_FILE = os.path.join(TMP, "arena.db")
os.environ.update({
    "ARENA_DB": DB_FILE,
    "ARENA_BASE_PATH": "/",
    "ARENA_SECRET": "test-secret",
    "SCHOOL_AUTH_ENABLED": "1",
    "SCHOOL_LOGIN_URL": f"http://127.0.0.1:{srv.server_address[1]}/Home/Login",
    "SCHOOL_EMAIL_DOMAIN": "stu.wfla-ailab.top",
    "TUTORIAL_DOMAIN": "tutorial.wfla-ailab.top",
    "WEBUI_ADMIN_EMAIL": "admin@example.com",
    "WEBUI_ADMIN_PASSWORD": "adminpw",
})

import app as arena  # noqa: E402
import school_auth  # noqa: E402

arena.app.config["TESTING"] = True
arena.app.config["SESSION_COOKIE_SECURE"] = False

# 假聊天站：不真的连 OpenWebUI
provisioned = {}


def fake_signin(email, password):
    if not email or not password:
        return None
    return "owu-token-" + email


def fake_http(method, url, payload=None, token=None, timeout=30):
    if url.endswith("/api/v1/auths/add"):
        provisioned[payload["email"]] = payload["password"]
        return 200, {"id": "owu-id-1", "email": payload["email"]}
    if "/api/v1/users/" in url and url.endswith("/update"):
        return 200, {"ok": True}
    return 404, "unexpected call: " + url


arena.owu_signin = fake_signin
arena.owu_admin_token = lambda: "admin-token"
arena.http = fake_http


def db_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    school_auth.ensure_tables(conn)
    return conn


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
@check("学校账号登录入口页面可打开")
def t_entry():
    with arena.app.test_client() as c:
        r = c.get("/school-login")
        assert r.status_code == 200, r.status_code
        assert "用学校账号登录" in r.get_data(as_text=True)


@check("开关关闭时 /school-login 退回普通登录")
def t_disabled():
    arena.SCHOOL_AUTH_ENABLED = False
    try:
        with arena.app.test_client() as c:
            r = c.get("/school-login")
            assert r.status_code == 302 and "/login" in r.headers["Location"], (r.status_code, r.headers.get("Location"))
    finally:
        arena.SCHOOL_AUTH_ENABLED = True


@check("密码错误 → 提示密码错误，且不建会话")
def t_bad_password():
    with arena.app.test_client() as c:
        r = c.post("/school-login", data={"code": "2025001", "password": "wrong"})
        body = r.get_data(as_text=True)
        assert r.status_code == 200 and "学校账号或密码错误" in body, body[:300]
        # 没登录：访问首页应被踢回登录页
        r2 = c.get("/")
        assert r2.status_code == 302, r2.status_code


@check("账号不存在 → 提示学校系统里没有这个账号")
def t_no_account():
    with arena.app.test_client() as c:
        r = c.post("/school-login", data={"code": "ghost", "password": "whatever"})
        body = r.get_data(as_text=True)
        assert "没有这个账号" in body, body[:300]


@check("学校密码正确但不在白名单 → 拦下并留下申请记录")
def t_not_whitelisted():
    with arena.app.test_client() as c:
        r = c.post("/school-login", data={"code": "2025001", "password": "school-pass"})
        body = r.get_data(as_text=True)
        assert "白名单" in body, body[:400]
        assert r.status_code == 200

    conn = db_conn()
    pend = [dict(x) for x in school_auth.pending_requests(conn)]
    assert len(pend) == 1 and pend[0]["code"] == "2025001", pend
    assert school_auth.list_entries(conn) == [], "不该自动把人加进白名单"
    conn.close()


@check("加入白名单后 → 登录成功、种 cookie、内部账号密码不是学校密码")
def t_login_ok():
    conn = db_conn()
    school_auth.add_entries(conn, [("2025001", "张三")], "2026-09-10 12:00:00")
    conn.close()

    with arena.app.test_client() as c:
        r = c.post("/school-login", data={"code": "2025001", "password": "school-pass"},
                   base_url="https://chat.wfla-ailab.top")
        assert r.status_code == 302 and r.headers["Location"].endswith("/"), (r.status_code, r.headers.get("Location"))
        cookies = r.headers.getlist("Set-Cookie")
        blob = " | ".join(cookies)
        assert "token=owu-token-2025001@stu.wfla-ailab.top" in blob, blob
        # Werkzeug 会把前导点去掉（Domain=wfla-ailab.top），RFC 6265 下浏览器
        # 依然把它当作覆盖所有子域名的 cookie，所以两种写法都接受。
        assert re.search(r"Domain=\.?wfla-ailab\.top", blob), blob
        # 登录态可用：首页能看到自己的名字
        home = c.get("/", base_url="https://chat.wfla-ailab.top")
        assert home.status_code == 200, home.status_code
        assert "张三" in home.get_data(as_text=True)

    conn = db_conn()
    row = conn.execute("SELECT * FROM school_accounts WHERE code='2025001'").fetchone()
    assert row is not None, "应建立内部账号映射"
    assert row["owu_email"] == "2025001@stu.wfla-ailab.top"
    assert row["owu_password"] and row["owu_password"] != "school-pass", \
        "内部账号密码必须是随机生成的，绝不能等于学生输入的学校密码"
    pending_left = [dict(x) for x in school_auth.pending_requests(conn)]
    assert pending_left == [], pending_left
    conn.close()


@check("重复登录：复用同一内部账号，不重复建号")
def t_relogin_reuses_account():
    before = dict(provisioned)
    with arena.app.test_client() as c:
        r = c.post("/school-login", data={"code": "2025001", "password": "school-pass"},
                   base_url="https://chat.wfla-ailab.top")
        assert r.status_code == 302, r.status_code
    assert provisioned == before, "第二次登录不该再调 /auths/add"


@check("非管理员进不了白名单后台")
def t_admin_gate():
    with arena.app.test_client() as c:
        r = c.get("/admin/allowlist")
        assert r.status_code == 302, "未登录应被重定向"

    conn = db_conn()
    conn.execute("INSERT INTO users(email,name,is_admin,created_at) VALUES(?,?,?,?)",
                 ("member@example.com", "普通社员", 0, "2026-09-10 12:00:00"))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE email='member@example.com'").fetchone()["id"]
    conn.close()

    with arena.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["uid"] = uid
        r = c.get("/admin/allowlist")
        assert r.status_code == 302, "普通社员应被重定向"


@check("管理员后台：粘贴名单批量加白名单")
def t_admin_bulk_add():
    conn = db_conn()
    conn.execute("INSERT INTO users(email,name,is_admin,created_at) VALUES(?,?,?,?)",
                 ("president@example.com", "社长", 1, "2026-09-10 12:00:00"))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE email='president@example.com'").fetchone()["id"]
    conn.close()

    with arena.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["uid"] = uid
        page = c.get("/admin/allowlist")
        assert page.status_code == 200, page.status_code
        assert "学校账号白名单" in page.get_data(as_text=True)

        r = c.post("/admin/allowlist",
                   data={"action": "add", "codes": "2025111,王五\n# 注释\n2025112\n2025111,王五"})
        body = r.get_data(as_text=True)
        assert "新增 2 个" in body, body[:400]

        # 停用 → 该账号立刻被拦
        r = c.post("/admin/allowlist", data={"action": "disable", "code": "2025111"})
        assert "已停用 1 个账号" in r.get_data(as_text=True)

        r = c.post("/admin/allowlist", data={"action": "remove", "code": "2025112"})
        assert "已从白名单删除 1 个账号" in r.get_data(as_text=True)

    conn = db_conn()
    assert school_auth.is_allowed(conn, "2025111") is None, "停用后不该放行"
    assert school_auth.find_entry(conn, "2025112") is None, "删除后不该存在"
    conn.close()


@check("白名单里勾了「管理员权限」的学号，登录后真的能进后台")
def t_allowlist_admin_role():
    conn = db_conn()
    school_auth.add_entries(conn, [("2024002", "副社长")], "2026-09-10 12:00:00", role="admin")
    conn.close()

    with arena.app.test_client() as c:
        r = c.post("/school-login", data={"code": "2024002", "password": "school-pass"},
                   base_url="https://chat.wfla-ailab.top")
        assert r.status_code == 302, r.status_code
        page = c.get("/admin/allowlist", base_url="https://chat.wfla-ailab.top")
        assert page.status_code == 200, "白名单里勾了管理员权限，登录后应能打开白名单后台"

    conn = db_conn()
    row = conn.execute("SELECT is_admin FROM users WHERE email=?",
                       ("2024002@stu.wfla-ailab.top",)).fetchone()
    assert row is not None, "应建立该学号的用户记录"
    assert row["is_admin"] == 1, f"白名单 role=admin 应传导到 users.is_admin，实际={dict(row)}"
    conn.close()


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

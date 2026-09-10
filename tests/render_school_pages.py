#!/usr/bin/env python3
"""把学校账号登录相关的页面渲染成静态 HTML，供人工/截图复核。

用法：
    python tests/render_school_pages.py            # 渲染到 .verify/render/*.html
    python tests/render_school_pages.py --serve    # 额外在本机 8099 起一个真服务，便于浏览器复核

用的是真实的 Flask 路由与模板，只有聊天站（OpenWebUI）与学校系统是假的。
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ARENA_DIR = os.path.join(ROOT, "arena")
OUT_DIR = os.path.join(ROOT, ".verify", "render")
sys.path.insert(0, ARENA_DIR)


# --------------------------------------------------------------------------
# 假学校系统：渲染复核时不碰真实学校接口。
# 2025099 = 学校账号真实存在、但不在白名单里（用来渲染「申请开通」那一页）。
# --------------------------------------------------------------------------
class SchoolHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        code = (form.get("code") or [""])[0]
        password = (form.get("password") or [""])[0]
        if code == "2025099" and password == "school-pass":
            body = {"ResultType": 0, "Message": "", "Data": {"Url": "/"}}
        elif code == "2025099":
            body = {"ResultType": 1, "Message": "Password error", "Data": None}
        else:
            body = {"ResultType": 1, "Message": "Login account does not exist", "Data": None}
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


_school = ThreadingHTTPServer(("127.0.0.1", 0), SchoolHandler)
threading.Thread(target=_school.serve_forever, daemon=True).start()

TMP = tempfile.mkdtemp()
DB_FILE = os.path.join(TMP, "render.db")
os.environ.update({
    "ARENA_DB": DB_FILE,
    "ARENA_BASE_PATH": "/",
    "ARENA_SECRET": "render-secret",
    "SCHOOL_AUTH_ENABLED": "1",
    "SCHOOL_LOGIN_URL": f"http://127.0.0.1:{_school.server_address[1]}/Home/Login",
    "SCHOOL_EMAIL_DOMAIN": "stu.wfla-ailab.top",
    "TUTORIAL_DOMAIN": "tutorial.wfla-ailab.top",
    "WEBUI_ADMIN_EMAIL": "admin@example.com",
    "WEBUI_ADMIN_PASSWORD": "adminpw",
})

import app as arena  # noqa: E402
import school_auth  # noqa: E402


# 假聊天站：渲染页面不需要真的连 OpenWebUI
def fake_signin(email, password):
    return "owu-token-" + email if email and password else None


def fake_http(method, url, payload=None, token=None, timeout=30):
    if url.endswith("/api/v1/auths/add"):
        return 200, {"id": "owu-id", "email": payload["email"]}
    if "/api/v1/users/" in url and url.endswith("/update"):
        return 200, {"ok": True}
    return 404, "unexpected call: " + url


arena.owu_signin = fake_signin
arena.owu_admin_token = lambda: "admin-token"
arena.http = fake_http
arena.app.config["SESSION_COOKIE_SECURE"] = False


def seed():
    # 先用应用自己的建表逻辑把 users 等表建好（db() 依赖请求上下文）
    with arena.app.app_context():
        arena.db()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    school_auth.ensure_tables(conn)
    school_auth.add_entries(
        conn,
        [("2025001", "张三"), ("2025002", "李四"), ("2025003", "王五")],
        "2026-09-05 09:12:00",
        note="高一新生",
    )
    school_auth.add_entries(conn, [("2024001", "老社员")], "2026-09-01 08:00:00",
                            role="admin", note="副社长")
    school_auth.set_active(conn, "2025003", False)
    school_auth.record_request(conn, "2025010", "赵六", "我是高一3班的，想参加周三活动",
                               "2026-09-09 20:41:00")
    conn.execute("INSERT INTO users(email,name,is_admin,created_at) VALUES(?,?,?,?)",
                 ("president@example.com", "社长", 1, "2026-08-01 10:00:00"))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE email='president@example.com'").fetchone()["id"]
    conn.close()
    return uid


def render():
    os.makedirs(OUT_DIR, exist_ok=True)
    uid = seed()
    written = []

    with arena.app.test_client() as c:
        # 1) 登录入口页
        html = c.get("/school-login").get_data(as_text=True)
        path = os.path.join(OUT_DIR, "school-login.html")
        open(path, "w", encoding="utf-8").write(html)
        written.append(path)

        # 2) 学校账号通过但不在白名单 → 申请页（假学校系统认这个账号）
        html = c.post("/school-login",
                      data={"code": "2025099", "password": "school-pass"}).get_data(as_text=True)
        assert "申请已记录" in html or "白名单" in html, "申请页没渲染出来"
        path = os.path.join(OUT_DIR, "school-login-pending.html")
        open(path, "w", encoding="utf-8").write(html)
        written.append(path)

        # 3) 管理员白名单后台
        with c.session_transaction() as sess:
            sess["uid"] = uid
        html = c.get("/admin/allowlist").get_data(as_text=True)
        path = os.path.join(OUT_DIR, "admin-allowlist.html")
        open(path, "w", encoding="utf-8").write(html)
        written.append(path)

    # 4) 普通登录页（带学校账号入口卡片）—— 用全新 client，避免带上上面的登录态
    with arena.app.test_client() as c2:
        html = c2.get("/login").get_data(as_text=True)
        path = os.path.join(OUT_DIR, "login.html")
        open(path, "w", encoding="utf-8").write(html)
        written.append(path)

    for p in written:
        print("wrote", p, os.path.getsize(p), "bytes")
    return written


if __name__ == "__main__":
    render()
    if "--serve" in sys.argv:
        print("serving http://127.0.0.1:8099/school-login （Ctrl+C 结束）")
        threading.Thread(target=lambda: arena.app.run(host="127.0.0.1", port=8099),
                         daemon=True).start()
        threading.Event().wait()

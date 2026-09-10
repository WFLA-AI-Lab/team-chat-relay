#!/usr/bin/env python3
"""school_auth 自测：不需要联网、不碰真实学校系统、不碰生产库。

用法：
    python tests/test_school_auth.py            # 离线单元测试（默认）
    python tests/test_school_auth.py --live     # 追加：真实学校系统连通性校验（只用一个不存在的账号探活）

离线部分会在本机起一个假学校系统（HTTP），复刻实测到的返回契约：
    ResultType=0 通过 / "Password error" / "Login account does not exist"
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "arena"))
import school_auth  # noqa: E402

REAL_URL = "http://101.227.232.33:8001/Home/Login"


# --------------------------------------------------------------------------
# 假学校系统
# --------------------------------------------------------------------------
class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        code = (form.get("code") or [""])[0]
        password = (form.get("password") or [""])[0]

        if code == "2025001" and password == "school-pass":
            body = {"ResultType": 0, "Message": "", "Data": {"Url": "/"}}
        elif code == "2025001":
            body = {"ResultType": 1, "Message": "Password error", "Data": None}
        else:
            body = {"ResultType": 1, "Message": "Login account does not exist", "Data": None}

        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class NotJsonHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = b"<html>maintenance</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --------------------------------------------------------------------------
# 测试用例
# --------------------------------------------------------------------------
CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("normalize_code 去空白")
def t_normalize():
    assert school_auth.normalize_code("  2025001 ") == "2025001"
    assert school_auth.normalize_code("202\u30005001") == "2025001"
    assert school_auth.normalize_code(None) == ""


@check("owu_email_for 生成稳定内部邮箱")
def t_email():
    assert school_auth.owu_email_for("2025001", "stu.wfla-ailab.top") == "2025001@stu.wfla-ailab.top"
    assert school_auth.owu_email_for("K.2025/01", "x.top") == "k.2025-01@x.top"


@check("verify：空账号/空密码不发请求")
def t_verify_empty():
    r = school_auth.verify("", "x", url="http://127.0.0.1:1/Home/Login")
    assert r["reason"] == school_auth.REASON_EMPTY, r
    r = school_auth.verify("2025001", "", url="http://127.0.0.1:1/Home/Login")
    assert r["reason"] == school_auth.REASON_EMPTY, r


@check("verify：连不上时归为 network，不抛异常")
def t_verify_network():
    r = school_auth.verify("2025001", "x", url="http://127.0.0.1:9/Home/Login", timeout=1)
    assert r["reason"] == school_auth.REASON_NETWORK, r


@check("verify：返回非 JSON 时归为 bad_reply")
def t_verify_badreply():
    srv = serve(NotJsonHandler)
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/Home/Login"
        r = school_auth.verify("2025001", "x", url=url, timeout=3)
        assert r["reason"] == school_auth.REASON_BAD_REPLY, r
    finally:
        srv.shutdown()


@check("verify：ResultType=0 → 通过（复刻实测契约）")
def t_verify_ok():
    srv = serve(FakeHandler)
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/Home/Login"
        r = school_auth.verify("2025001", "school-pass", url=url, timeout=3)
        assert r["ok"] and r["reason"] == school_auth.REASON_OK, r
        assert r["raw"]["ResultType"] == 0
    finally:
        srv.shutdown()


@check("verify：'Password error' → bad_password")
def t_verify_badpw():
    srv = serve(FakeHandler)
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/Home/Login"
        r = school_auth.verify("2025001", "wrong", url=url, timeout=3)
        assert not r["ok"] and r["reason"] == school_auth.REASON_BAD_PASSWORD, r
        assert "密码" in r["message"], r
    finally:
        srv.shutdown()


@check("verify：'does not exist' → no_account")
def t_verify_noacct():
    srv = serve(FakeHandler)
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/Home/Login"
        r = school_auth.verify("ghost", "x", url=url, timeout=3)
        assert r["reason"] == school_auth.REASON_NO_ACCOUNT, r
        assert "没有这个账号" in r["message"], r
    finally:
        srv.shutdown()


@check("parse_bulk：一行一个学号 + 姓名，容忍逗号/空格/注释")
def t_parse_bulk():
    text = "2025001,张三\n# 注释行\n2025002 李四\n\n2025001,重复\n2025003\t王五\n"
    entries = school_auth.parse_bulk(text)
    assert entries == [("2025001", "张三"), ("2025002", "李四"), ("2025003", "王五")], entries


@check("白名单：加入→放行→停用→拦截→删除")
def t_allowlist_flow():
    tmp = tempfile.mkdtemp()
    conn = sqlite3.connect(os.path.join(tmp, "t.db"))
    conn.row_factory = sqlite3.Row
    school_auth.ensure_tables(conn)

    assert school_auth.is_allowed(conn, "2025001") is None

    added, updated = school_auth.add_entries(conn, [("2025001", "张三")], "2026-09-10 10:00:00")
    assert (added, updated) == (1, 0), (added, updated)
    row = school_auth.is_allowed(conn, "2025001")
    assert row is not None and row["name"] == "张三", dict(row)

    # 重复加入：改为更新，不产生第二条
    added, updated = school_auth.add_entries(conn, [("2025001", "张三")], "2026-09-10 10:05:00")
    assert (added, updated) == (0, 1), (added, updated)
    assert len(school_auth.list_entries(conn)) == 1

    assert school_auth.set_active(conn, "2025001", False) == 1
    assert school_auth.is_allowed(conn, "2025001") is None
    assert school_auth.set_active(conn, "2025001", True) == 1
    assert school_auth.is_allowed(conn, "2025001") is not None

    assert school_auth.remove_entry(conn, "2025001") == 1
    assert school_auth.is_allowed(conn, "2025001") is None
    conn.close()


@check("申请记录：不在白名单的人留档，加入白名单后自动消掉")
def t_join_requests():
    tmp = tempfile.mkdtemp()
    conn = sqlite3.connect(os.path.join(tmp, "t2.db"))
    conn.row_factory = sqlite3.Row
    school_auth.ensure_tables(conn)

    school_auth.record_request(conn, "2025999", "赵六", "想加入", "2026-09-10 11:00:00")
    pend = school_auth.pending_requests(conn)
    assert len(pend) == 1 and pend[0]["code"] == "2025999", [dict(r) for r in pend]

    school_auth.add_entries(conn, [("2025999", "赵六")], "2026-09-10 11:05:00")
    assert school_auth.pending_requests(conn) == [], "加入白名单后申请应自动标记已处理"
    conn.close()


@check("ensure_tables 幂等：重复调用不报错")
def t_tables_idempotent():
    tmp = tempfile.mkdtemp()
    conn = sqlite3.connect(os.path.join(tmp, "t3.db"))
    school_auth.ensure_tables(conn)
    school_auth.ensure_tables(conn)
    conn.close()


def test_live():
    """真实学校系统连通性：只用一个不存在的账号探活，不用任何真实凭据。"""
    r = school_auth.verify("dshtest_not_exist_zzz", "probe", url=REAL_URL, timeout=10)
    print(f"   真实端点返回：reason={r['reason']} message={r['message']}")
    assert r["reason"] == school_auth.REASON_NO_ACCOUNT, (
        "真实学校系统的返回契约和预期不一致，请重新确认接口：%r" % (r,)
    )


def main():
    live = "--live" in sys.argv
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

    if live:
        print("  ---- 真实学校系统探活 ----")
        try:
            test_live()
            print("  PASS  真实端点契约一致")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  真实端点探活\n        {type(exc).__name__}: {exc}")

    total = len(CHECKS) + (1 if live else 0)
    print(f"\n{total - failed}/{total} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

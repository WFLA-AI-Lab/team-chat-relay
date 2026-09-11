#!/usr/bin/env python3
"""白名单收紧（enforce_allowlist.py）自测 —— 不联网、不碰生产、不碰真聊天站。

守三件事：
  1. 分类正确：白名单内/管理员/已 pending 的账号保留，其余降级为 pending；
  2. 默认 dry-run 绝不改任何账号（--apply 才调降级接口）；
  3. 危险情况要硬失败：拿不到管理员 token、读不到用户列表 → 退出码 1，不动手。

用法：python tests/test_enforce_allowlist.py
"""

import io
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ARENA_DIR = os.path.join(ROOT, "arena")
sys.path.insert(0, ARENA_DIR)

TMP = tempfile.mkdtemp()
DB_FILE = os.path.join(TMP, "arena.db")

os.environ.update({
    "ARENA_DB": DB_FILE,
    "OPENWEBUI_URL": "http://stub-openwebui:8080",
    "WEBUI_ADMIN_EMAIL": "admin@example.com",
    "WEBUI_ADMIN_PASSWORD": "adminpw",
    "SCHOOL_EMAIL_DOMAIN": "stu.wfla-ailab.top",
})

import school_auth  # noqa: E402
import enforce_allowlist as ea  # noqa: E402

# ---- 白名单：2025001 在用、2025002 已停用 --------------------------------
conn = sqlite3.connect(DB_FILE)
school_auth.ensure_tables(conn)
conn.execute("INSERT INTO allowlist(code,name,role,active,added_at) "
             "VALUES('2025001','甲','member',1,'2026-09-10 10:00:00')")
conn.execute("INSERT INTO allowlist(code,name,role,active,added_at) "
             "VALUES('2025002','乙','member',0,'2026-09-10 10:00:00')")
conn.commit()
conn.close()

USERS = [
    {"id": "u1", "email": "admin@example.com", "role": "admin"},
    {"id": "u2", "email": "2025001@stu.wfla-ailab.top", "role": "user"},
    {"id": "u3", "email": "2025002@stu.wfla-ailab.top", "role": "user"},
    {"id": "u4", "email": "olduser@example.com", "role": "user"},
    {"id": "u5", "email": "held@example.com", "role": "pending"},
    {"id": "u6", "email": "TEACHER@wfla-ailab.top", "role": "user"},
    {"id": "u7", "email": "", "role": "user"},
]

CALLS = []


def install_fake_http(users=None):
    """把打网络的 http() 换成记录调用并返回固定结果的假实现。"""
    CALLS.clear()

    def fake_http(method, url, payload=None, token=None, timeout=30):
        CALLS.append({"method": method, "url": url, "payload": payload, "token": token})
        if url.endswith("/api/v1/auths/signin"):
            return 200, {"token": "admin-token"}
        if url.endswith("/api/v1/users/"):
            if users is None:
                return 500, "boom"
            return 200, {"users": users}
        if "/api/v1/users/" in url and url.endswith("/update"):
            return 200, {"ok": True}
        return 404, "unexpected call: " + url

    ea.http = fake_http
    return fake_http


def updates():
    return [c for c in CALLS if c["url"].endswith("/update")]


def run_main(argv):
    """跑一次 main()，把 stdout 与 stderr 一起收进来（错误信息走 stderr）。"""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = ea.main(argv)
    return code, buf.getvalue()


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
@check("load_allowed_emails：只取在用（active=1）的白名单账号，映射成聊天站邮箱")
def t_load_allowed():
    allowed = ea.load_allowed_emails(DB_FILE, "stu.wfla-ailab.top")
    assert allowed == {"2025001@stu.wfla-ailab.top"}, allowed


@check("分类：白名单内保留；管理员保留；已 pending 保留；其余才降级")
def t_classify():
    block, keep = ea.classify(USERS, {"2025001@stu.wfla-ailab.top"}, "admin@example.com")
    blocked = [u["email"] for u in block]
    kept = {u["email"]: why for u, why in keep}

    assert "2025001@stu.wfla-ailab.top" not in blocked, "白名单里的人不该被降级"
    assert "admin@example.com" not in blocked, "管理员永远不降级"
    assert "held@example.com" not in blocked, "已经是 pending 的不重复处理"
    assert "" not in blocked, "没有邮箱的用户跳过"
    assert blocked == ["2025002@stu.wfla-ailab.top", "olduser@example.com",
                       "TEACHER@wfla-ailab.top"], blocked
    assert "管理员，永不降级" in kept["admin@example.com"], kept
    assert "在白名单里" in kept["2025001@stu.wfla-ailab.top"], kept


@check("邮箱比较大小写不敏感；--include 里的额外账号保留")
def t_case_insensitive_and_include():
    block, _ = ea.classify(USERS, {"2025001@STU.WFLA-AILAB.TOP"})
    assert "2025001@stu.wfla-ailab.top" not in [u["email"] for u in block], \
        "白名单比对应忽略大小写"

    block2, keep2 = ea.classify(USERS, {"2025001@stu.wfla-ailab.top"}, "",
                                ("teacher@wfla-ailab.top",))
    assert "TEACHER@wfla-ailab.top" not in [u["email"] for u in block2], \
        "--include 应放行（同样忽略大小写）"
    assert any(u["email"] == "TEACHER@wfla-ailab.top" for u, _ in keep2)


@check("默认 dry-run：打印名单但一个账号都不改")
def t_dry_run_no_writes():
    install_fake_http(USERS)
    code, out = run_main(["--db", DB_FILE])
    assert code == 0, code
    assert updates() == [], f"dry-run 不该调降级接口：{updates()}"
    assert "dry-run" in out, out
    assert "2025002@stu.wfla-ailab.top" in out, "应列出会被处理的账号"
    assert "olduser@example.com" in out, out


@check("--apply：只降级该降级的账号，且用的是 role=pending（不删用户）")
def t_apply_downgrades():
    install_fake_http(USERS)
    code, out = run_main(["--apply", "--db", DB_FILE])
    assert code == 0, code

    touched = {c["url"].rsplit("/", 2)[-2] for c in updates()}
    assert touched == {"u3", "u4", "u6"}, f"应只动这三个账号，实际 {touched}"
    assert all(c["payload"] == {"role": "pending"} for c in updates()), "只改 role"
    assert not any("/delete" in c["url"] for c in CALLS), "绝不能删用户"
    assert "3 个已降级" in out, out


@check("--apply --include：被 include 的账号不动")
def t_apply_with_include():
    install_fake_http(USERS)
    code, _ = run_main(["--apply", "--db", DB_FILE, "--include", "teacher@wfla-ailab.top"])
    assert code == 0, code
    touched = {c["url"].rsplit("/", 2)[-2] for c in updates()}
    assert touched == {"u3", "u4"}, f"include 的账号不该被动：{touched}"


@check("拿不到管理员 token → 退出码 1，不读用户、不改账号")
def t_no_admin_token():
    def fake_http(method, url, payload=None, token=None, timeout=30):
        CALLS.append({"method": method, "url": url, "payload": payload, "token": token})
        return 401, "bad credentials"

    ea.http = fake_http
    CALLS.clear()
    code, out = run_main(["--apply", "--db", DB_FILE])
    assert code == 1, f"拿不到 token 应退出 1，实际 {code}\n{out}"
    assert updates() == [], "拿不到 token 时绝不能改任何账号"
    assert "拿不到聊天站管理员 token" in out, out


@check("读不到用户列表 → 退出码 1，不改账号")
def t_no_user_list():
    install_fake_http(users=None)
    code, out = run_main(["--apply", "--db", DB_FILE])
    assert code == 1, f"应退出 1，实际 {code}\n{out}"
    assert updates() == [], "读不到列表时绝不能改任何账号"
    assert "读不到聊天站用户列表" in out, out


@check("白名单为空时也绝不误伤管理员（只降级普通账号）")
def t_empty_allowlist_safe():
    empty_db = os.path.join(TMP, "empty.db")
    c = sqlite3.connect(empty_db)
    school_auth.ensure_tables(c)
    c.commit()
    c.close()

    install_fake_http(USERS)
    code, out = run_main(["--apply", "--db", empty_db])
    assert code == 0, code
    touched = {c["url"].rsplit("/", 2)[-2] for c in updates()}
    assert "u1" not in touched, "管理员绝不能被降级"
    assert "u5" not in touched, "已是 pending 的不动"
    assert "白名单是空的" in out, "空白名单应给出明确警告：" + out


@check("降级失败要如实报错并返回 1（不漏报）")
def t_partial_failure():
    def fake_http(method, url, payload=None, token=None, timeout=30):
        CALLS.append({"method": method, "url": url, "payload": payload, "token": token})
        if url.endswith("/api/v1/auths/signin"):
            return 200, {"token": "admin-token"}
        if url.endswith("/api/v1/users/"):
            return 200, {"users": USERS}
        if "/api/v1/users/" in url and url.endswith("/update"):
            return 500, "cannot downgrade"
        return 404, "unexpected"

    ea.http = fake_http
    CALLS.clear()
    code, out = run_main(["--apply", "--db", DB_FILE])
    assert code == 1, f"有账号没降级成功就该退出 1，实际 {code}\n{out}"
    assert "FAIL" in out and "失败" in out, out


@check("命令行契约：--help 退出 0 并说明 --apply")
def t_cli_contract():
    install_fake_http(USERS)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            ea.main(["--help"])
        code = 0
    except SystemExit as exc:
        code = exc.code or 0
    assert code == 0, code
    assert "--apply" in buf.getvalue(), buf.getvalue()


@check("包装脚本存在且真的在容器里调这个 python 文件")
def t_wrapper_script():
    path = os.path.join(ROOT, "scripts", "enforce-allowlist.sh")
    assert os.path.exists(path), "缺 scripts/enforce-allowlist.sh"
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()
    assert "exec -T arena" in body, "应在 arena 容器里执行"
    assert "/app/enforce_allowlist.py" in body, "应调用 arena/enforce_allowlist.py"
    assert '"$@"' in body, "应把参数透传给 python（否则 --apply 传不进去）"


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

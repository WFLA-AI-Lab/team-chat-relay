#!/usr/bin/env python3
"""白名单收紧：把不在白名单里的聊天站账号降级为「待审批」(pending)。

背景（交接文档 §6 的已知缺口 P1-3）：
  /arena/school-login 那条路本来就查白名单；但**聊天站自己的登录表单**
  （以及教程站 /login 走的 OWU signin）不查白名单 —— 于是"以前手动建过
  的账号"仍然能登录，白名单只闸住了一条路。

  聊天站的登录接口属于 Open WebUI，不在我们代码里，没法在中间拦截；
  但 Open WebUI 有 role 概念：role='pending' 的账号无法登录。
  所以本脚本就是"让白名单真正生效"的那一步：
      取聊天站用户列表 → 与白名单比对 → 把不该进来的账号降级为 pending。

安全约定（与 school_auth 一致，改这个文件请守住）：
  * 只改 role，**绝不删除用户、绝不动密码**；
  * role=admin 的用户和 WEBUI_ADMIN_EMAIL 永远不碰；
  * 默认 dry-run（只看不改），确认无误再加 --apply。

用法（服务器上，容器里跑，见 scripts/enforce-allowlist.sh）：
    python enforce_allowlist.py                       # 预览会动谁
    python enforce_allowlist.py --apply               # 真的降级
    python enforce_allowlist.py --apply --include teacher@wfla-ailab.top
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

import school_auth

OPENWEBUI_URL = os.environ.get("OPENWEBUI_URL", "http://open-webui:8080").rstrip("/")
ADMIN_EMAIL = os.environ.get("WEBUI_ADMIN_EMAIL", "").strip()
ADMIN_PASSWORD = os.environ.get("WEBUI_ADMIN_PASSWORD", "")
SCHOOL_EMAIL_DOMAIN = os.environ.get("SCHOOL_EMAIL_DOMAIN", "stu.wfla-ailab.top").strip()
DB_PATH = os.environ.get("ARENA_DB", "/data/arena.db")
BLOCKED_ROLE = "pending"


# --------------------------------------------------------------------------
# HTTP（与 arena/app.py 同风格：只用标准库，不发第三方请求）
# --------------------------------------------------------------------------
def http(method, url, payload=None, token=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def owu_admin_token():
    status, body = http("POST", f"{OPENWEBUI_URL}/api/v1/auths/signin",
                        {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if status == 200 and isinstance(body, dict):
        return body.get("token")
    return None


def owu_list_users(token):
    """管理员接口拉用户列表；失败返回 None。"""
    status, body = http("GET", f"{OPENWEBUI_URL}/api/v1/users/", token=token)
    if status != 200:
        return None
    users = body.get("users") if isinstance(body, dict) else body
    return users if isinstance(users, list) else None


def owu_set_role(token, user_id, role):
    return http("POST", f"{OPENWEBUI_URL}/api/v1/users/{user_id}/update",
                {"role": role}, token=token)


# --------------------------------------------------------------------------
# 白名单
# --------------------------------------------------------------------------
def load_allowed_emails(db_path=None, domain=None):
    """白名单里 active=1 的学号 → 聊天站内部邮箱集合。"""
    path = db_path or DB_PATH
    domain = domain or SCHOOL_EMAIL_DOMAIN
    conn = sqlite3.connect(path)
    try:
        school_auth.ensure_tables(conn)
        rows = conn.execute("SELECT code FROM allowlist WHERE active=1").fetchall()
    finally:
        conn.close()
    return {school_auth.owu_email_for(row[0], domain) for row in rows}


def classify(users, allowed_emails, admin_email="", include=()):
    """把用户分成 (要降级的, [(用户, 原因)])。

    纯函数（不打网络），所以能被测试直接断言每一类边界情况。
    """
    allow = {str(e).strip().lower() for e in allowed_emails if e}
    allow |= {str(e).strip().lower() for e in (include or ()) if e}
    admin_email = (admin_email or "").strip().lower()

    block, keep = [], []
    for u in users or []:
        if not isinstance(u, dict):
            continue
        email = str(u.get("email") or "").strip().lower()
        role = str(u.get("role") or "user").strip().lower()
        if not email:
            keep.append((u, "没有邮箱，跳过"))
        elif role == "admin" or (admin_email and email == admin_email):
            keep.append((u, "管理员，永不降级"))
        elif email in allow:
            keep.append((u, "在白名单里"))
        elif role == BLOCKED_ROLE:
            keep.append((u, "已经是待审批"))
        else:
            block.append(u)
    return block, keep


# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="把不在白名单里的聊天站账号降级为 pending（默认只看不改）")
    parser.add_argument("--apply", action="store_true",
                        help="真的执行降级；不加则只预览（dry-run）")
    parser.add_argument("--include", action="append", default=[],
                        metavar="EMAIL", help="额外放行的邮箱（可重复，如老师账号）")
    parser.add_argument("--db", default=DB_PATH, help=f"擂台库路径（默认 {DB_PATH}）")
    parser.add_argument("--domain", default=SCHOOL_EMAIL_DOMAIN,
                        help="学号映射域名（默认 %(default)s）")
    args = parser.parse_args(argv)

    print("== 白名单收紧（%s）==" % ("APPLY 真改" if args.apply else "dry-run 预览"))

    allowed = load_allowed_emails(args.db, args.domain)
    print(f"白名单在用账号：{len(allowed)} 个")
    if not allowed:
        print("⚠️  白名单是空的 —— 若现在 --apply，会把所有非管理员账号都降级。")

    token = owu_admin_token()
    if not token:
        print("错误：拿不到聊天站管理员 token（检查 WEBUI_ADMIN_EMAIL / "
              "WEBUI_ADMIN_PASSWORD 与容器连通性）", file=sys.stderr)
        return 1

    users = owu_list_users(token)
    if users is None:
        print("错误：读不到聊天站用户列表", file=sys.stderr)
        return 1
    print(f"聊天站用户：{len(users)} 个")

    block, keep = classify(users, allowed, ADMIN_EMAIL, args.include)

    print(f"\n-- 保留 {len(keep)} 个 --")
    for u, why in keep:
        print(f"   keep   {u.get('email')}  ({why})")

    print(f"\n-- 降级为 {BLOCKED_ROLE} 的 {len(block)} 个 --")
    for u in block:
        print(f"   block  {u.get('email')}  (role={u.get('role')})")

    if not block:
        print("\n没有需要处理的账号，白名单已经是生效状态。")
        return 0

    if not args.apply:
        print("\n（dry-run：没有改动任何账号。确认上面这份名单没问题后，"
              "加 --apply 重新运行即可生效。）")
        return 0

    ok = failed = 0
    for u in block:
        status, body = owu_set_role(token, u.get("id"), BLOCKED_ROLE)
        if status == 200:
            ok += 1
            print(f"   done   {u.get('email')} → {BLOCKED_ROLE}")
        else:
            failed += 1
            detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            print(f"   FAIL   {u.get('email')}：HTTP {status} {str(detail)[:120]}")

    print(f"\n完成：{ok} 个已降级，{failed} 个失败。"
          "这些账号现在无法登录聊天站/教程站，直到被加进白名单。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

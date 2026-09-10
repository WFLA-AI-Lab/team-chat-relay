#!/usr/bin/env bash
# 部署「学校账号登录」之后，在服务器上跑一遍体检。
#
# 用法（在服务器上）：
#     cd /opt/team-chat-relay
#     bash scripts/smoke-school-auth.sh
#
# 只做只读探活，不建任何账号、不改任何数据。
# 用一个**不存在的**账号去探学校系统的返回契约，不需要也不应该用真实学号。

set -u
cd "$(dirname "$0")/.." || exit 1

echo "== 1. 容器状态 =="
docker compose ps arena 2>&1

echo
echo "== 2. 学校系统连通性 + 返回契约 =="
docker compose exec -T arena python - <<'PY'
import os

import school_auth

print("SCHOOL_AUTH_ENABLED =", os.environ.get("SCHOOL_AUTH_ENABLED", "(未设置 → 默认 0 关闭)"))
print("SCHOOL_LOGIN_URL    =", school_auth.login_url())
r = school_auth.verify("dshtest_not_exist_zzz", "probe")
print("verify() ->", "ok=" + str(r["ok"]), "reason=" + r["reason"], "|", r["message"])
if r["reason"] == "no_account":
    print("PASS 容器能访问学校系统，且返回契约与实现一致")
elif r["reason"] == "network":
    print("FAIL 容器连不上学校系统：检查出网/防火墙，或学校系统只允许校内网访问")
elif r["reason"] == "bad_reply":
    print("FAIL 学校接口返回变了：请重新抓包确认字段（见 .verify/school-account-plan.md）")
else:
    print("WARN 返回不符合预期：", r)
PY

echo
echo "== 3. 聊天站管理员接口（自动开通内部账号要靠它）=="
docker compose exec -T arena python - <<'PY'
import app

token = app.owu_admin_token()
print("管理员登录聊天站:", "PASS" if token else "FAIL —— 检查 WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD")
if token:
    status, body = app.http("GET", app.OPENWEBUI_URL + "/api/v1/users/", token=token)
    ok = status == 200 and isinstance(body, (dict, list))
    print("GET /api/v1/users/ ->", status, "PASS" if ok else "FAIL（拿不到用户列表，开通流程会报错）")
    print("提示：创建用户走 POST /api/v1/auths/add，密码重置走 POST /api/v1/users/<id>/update")
PY

echo
echo "== 4. 白名单现状 =="
docker compose exec -T arena python - <<'PY'
import os
import sqlite3

path = os.environ.get("ARENA_DB", "/data/arena.db")
if not os.path.exists(path):
    print("还没有数据库文件（说明 arena 还没跑过请求）")
    raise SystemExit
conn = sqlite3.connect(path)
conn.row_factory = sqlite3.Row
try:
    rows = conn.execute("SELECT code, name, role, active FROM allowlist ORDER BY code").fetchall()
except sqlite3.OperationalError as exc:
    print("白名单表还没建（arena 重启后会建）：", exc)
    raise SystemExit
print(f"白名单共 {len(rows)} 条：")
for r in rows:
    print("  ", r["code"], r["name"] or "-", r["role"], "启用" if r["active"] else "停用")
pending = conn.execute("SELECT COUNT(*) AS c FROM join_requests WHERE handled=0").fetchone()["c"]
print("待处理申请：", pending)
PY

echo
echo "== 完成。若第 2、3 步都是 PASS，就可以把 SCHOOL_AUTH_ENABLED 打开并通知社员了 =="

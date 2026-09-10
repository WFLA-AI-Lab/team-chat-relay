#!/usr/bin/env python3
"""学校统一认证 + 白名单（school_auth）。

目标：让社团平台的登录凭据 = 学校综合系统（WFLA高中综合系统）的账号密码，
并且只允许社长手动加入白名单的账号进入。

事实依据（2026-09-10 实测 http://101.227.232.33:8001）：
  * 登录接口：POST /Home/Login，表单字段 code / password / remember
  * 返回 JSON：{"ResultType":0|1,"Message":"...","Data":{...}}
      - ResultType == 0 → 校验通过，Data 里带 Url（学校系统的跳转地址）
      - 账号不存在     → {"ResultType":1,"Message":"Login account does not exist"}
      - 密码错误       → {"ResultType":1,"Message":"Password error"}
  * 无验证码、无 CSRF token，因此可以在服务端代为校验。

安全约定（写在代码里，避免后来人改错）：
  1. 学生输入的学校密码**只用于这一次校验**，用完即弃：不落库、不写日志、不进 session。
  2. 平台内部账号（聊天站 OpenWebUI）密码由服务端随机生成，只存本地库，
     学生不需要、也看不到它。
  3. 本模块不抓取学校系统的任何学生数据，只读取登录校验结果。
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_LOGIN_URL = "http://101.227.232.33:8001/Home/Login"
DEFAULT_TIMEOUT = 8.0
USER_AGENT = "WFLAAILabPortal/1.0 (+https://wfla-ailab.top)"

# verify() 的 reason 取值
REASON_OK = "ok"
REASON_NO_ACCOUNT = "no_account"
REASON_BAD_PASSWORD = "bad_password"
REASON_NETWORK = "network"
REASON_BAD_REPLY = "bad_reply"
REASON_EMPTY = "empty"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def login_url():
    return os.environ.get("SCHOOL_LOGIN_URL", DEFAULT_LOGIN_URL)


def normalize_code(raw):
    """学号/工号归一化：去空白（含全角空格）。"""
    code = (raw or "").strip()
    code = code.replace(" ", "").replace("\u3000", "")
    return code[:64]


def owu_email_for(code, domain="stu.wfla-ailab.top"):
    """把学号映射成聊天站内部邮箱（学生看不到，只作为平台账号标识）。"""
    slug = re.sub(r"[^0-9a-z._-]+", "-", normalize_code(code).lower()).strip("-.")
    return f"{slug or 'user'}@{domain.lstrip('@')}"


def _col(row, name, index):
    """兼容 sqlite3.Row 与普通 tuple 取值。"""
    try:
        return row[name]
    except (TypeError, IndexError, KeyError):
        return row[index]


# --------------------------------------------------------------------------
# 学校系统校验（唯一与学校系统通信的地方）
# --------------------------------------------------------------------------
def verify(code, password, url=None, timeout=None):
    """拿学校账号密码去学校系统换一次校验结果。

    返回 {"ok": bool, "reason": str, "message": str, "raw": dict|None}
    reason ∈ ok / no_account / bad_password / network / bad_reply / empty
    message 是可以直接展示给学生的中文提示（失败时）。
    """
    code = normalize_code(code)
    if not code or not password:
        return {"ok": False, "reason": REASON_EMPTY,
                "message": "学校账号和密码都要填", "raw": None}

    payload = urllib.parse.urlencode(
        {"code": code, "password": password, "remember": "false"}
    ).encode("utf-8")
    req = urllib.request.Request(
        url or login_url(),
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or DEFAULT_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "reason": REASON_BAD_REPLY,
                "message": f"学校系统返回 HTTP {exc.code}，请稍后再试", "raw": None}
    except Exception as exc:  # noqa: BLE001 —— 超时 / 拒绝连接 / DNS 失败
        return {"ok": False, "reason": REASON_NETWORK,
                "message": f"连不上学校系统（{exc}），请稍后再试或联系社长", "raw": None}

    try:
        data = json.loads(body)
    except ValueError:
        return {"ok": False, "reason": REASON_BAD_REPLY,
                "message": "学校系统返回了非预期内容：" + body.strip()[:80], "raw": None}

    if not isinstance(data, dict):
        return {"ok": False, "reason": REASON_BAD_REPLY,
                "message": "学校系统返回格式异常", "raw": None}

    if str(data.get("ResultType", "")).strip() == "0":
        return {"ok": True, "reason": REASON_OK, "message": "", "raw": data}

    message = str(data.get("Message") or "").strip()
    low = message.lower()
    if "does not exist" in low or "not exist" in low or "不存在" in message:
        return {"ok": False, "reason": REASON_NO_ACCOUNT, "raw": data,
                "message": "学校系统里没有这个账号，请检查学号/工号"}
    if "password" in low or "密码" in message:
        return {"ok": False, "reason": REASON_BAD_PASSWORD, "raw": data,
                "message": "学校账号或密码错误"}
    return {"ok": False, "reason": REASON_BAD_REPLY, "raw": data,
            "message": message or "学校系统拒绝了这次登录"}


# --------------------------------------------------------------------------
# 白名单 / 内部账号映射（本地数据库）
# --------------------------------------------------------------------------
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS allowlist(
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'member',
    active INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS school_accounts(
    code TEXT PRIMARY KEY,
    owu_email TEXT NOT NULL,
    owu_password TEXT NOT NULL,
    owu_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    last_login TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS join_requests(
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    handled INTEGER NOT NULL DEFAULT 0);
"""


def ensure_tables(conn):
    conn.executescript(CREATE_SQL)


def parse_bulk(text):
    """解析社长粘贴的名单：每行 `学号` / `学号,姓名` / `学号 姓名`，# 开头是注释。"""
    entries, seen = [], set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[,，、\t]|\s+", line, maxsplit=1)
        code = normalize_code(parts[0])
        if not code or code in seen:
            continue
        seen.add(code)
        entries.append((code, parts[1].strip() if len(parts) > 1 else ""))
    return entries


def add_entries(conn, entries, now_str, role="member", note=""):
    """批量加白名单；已存在的账号会被重新启用。返回 (新增数, 更新数)。"""
    added = updated = 0
    for code, name in entries:
        row = conn.execute("SELECT name FROM allowlist WHERE code=?", (code,)).fetchone()
        if row:
            conn.execute(
                "UPDATE allowlist SET active=1, name=?, role=? WHERE code=?",
                (name or _col(row, "name", 0), role, code),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO allowlist(code,name,role,active,note,added_at) VALUES(?,?,?,1,?,?)",
                (code, name, role, note, now_str),
            )
            added += 1
        conn.execute("UPDATE join_requests SET handled=1 WHERE code=?", (code,))
    conn.commit()
    return added, updated


def find_entry(conn, code):
    return conn.execute("SELECT * FROM allowlist WHERE code=?", (normalize_code(code),)).fetchone()


def is_allowed(conn, code):
    """返回允许进入的名单行；不在名单里或已停用则返回 None。"""
    row = find_entry(conn, code)
    if row is None:
        return None
    return row if _col(row, "active", 3) else None


def list_entries(conn):
    return conn.execute("SELECT * FROM allowlist ORDER BY added_at DESC, code ASC").fetchall()


def remove_entry(conn, code):
    cur = conn.execute("DELETE FROM allowlist WHERE code=?", (normalize_code(code),))
    conn.commit()
    return cur.rowcount


def set_active(conn, code, active):
    cur = conn.execute("UPDATE allowlist SET active=? WHERE code=?",
                       (1 if active else 0, normalize_code(code)))
    conn.commit()
    return cur.rowcount


def record_request(conn, code, name, message, now_str):
    """记录「账号验证通过但不在白名单」的申请，供社长在后台看到。"""
    code = normalize_code(code)
    if not code:
        return
    conn.execute(
        "INSERT INTO join_requests(code,name,message,created_at,handled) VALUES(?,?,?,?,0) "
        "ON CONFLICT(code) DO UPDATE SET name=excluded.name, message=excluded.message,"
        " created_at=excluded.created_at, handled=0",
        (code, name, message, now_str),
    )
    conn.commit()


def pending_requests(conn):
    return conn.execute(
        "SELECT * FROM join_requests WHERE handled=0 ORDER BY created_at DESC"
    ).fetchall()

#!/usr/bin/env python3
"""管理员数据看板 + 站内公告自测（不联网、不碰生产库）。

守两件事：
  1. 看板上的每个数字都必须等于 sqlite 手查的数字（不能是"看起来像"）；
  2. 公告写进 settings 表后全站出现横幅，清空即撤下，且只有管理员能改。

用法：python tests/test_admin_stats.py
"""

import datetime
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ARENA_DIR = os.path.normpath(os.path.join(HERE, "..", "arena"))
sys.path.insert(0, ARENA_DIR)

TMP = tempfile.mkdtemp()
DB_FILE = os.path.join(TMP, "arena.db")
os.environ.update({
    "ARENA_DB": DB_FILE,
    "ARENA_BASE_PATH": "/",
    "ARENA_SECRET": "test-secret",
    "WEBUI_ADMIN_EMAIL": "admin@example.com",
})

import app as arena  # noqa: E402

arena.app.config["TESTING"] = True
arena.app.config["SESSION_COOKIE_SECURE"] = False


def conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def ts(days_ago=0):
    t = datetime.datetime.now() - datetime.timedelta(days=days_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


with arena.app.app_context():
    arena.db()          # 建表（含 school_auth 的 allowlist / join_requests）

USERS = {}


def seed():
    """造一批数据，覆盖看板上的每一类统计。"""
    c = conn()
    for email, name, is_admin in (("admin@example.com", "社长", 1),
                                  ("m1@example.com", "社员一", 0),
                                  ("m2@example.com", "社员二", 0)):
        c.execute("INSERT INTO users(email,name,is_admin,created_at) VALUES(?,?,?,?)",
                  (email, name, is_admin, ts(30)))
    c.commit()
    for row in c.execute("SELECT id,email FROM users"):
        USERS[row["email"]] = row["id"]

    # agents: 2 待审 / 1 上架 / 1 拒绝
    for i, status in enumerate(("pending", "pending", "approved", "rejected")):
        c.execute("INSERT INTO agents(model_id,name,system,model,author_id,status,created_at) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (f"agent-{i}", f"Agent{i}", "你是助手", "deepseek-chat",
                   USERS["m1@example.com"], status, ts(3)))

    # projects: 1 待审 / 2 上架
    for i, status in enumerate(("pending", "approved", "approved")):
        c.execute("INSERT INTO projects(user_id,repo_url,display_name,github_owner,github_repo,"
                  "status,created_at) VALUES(?,?,?,?,?,?,?)",
                  (USERS["m1@example.com"], f"https://github.com/wf/a{i}", f"作品{i}",
                   "wf", f"a{i}", status, ts(2)))

    # 试聊：今日 2 条，10 天前 1 条（不该算进「近 7 天」）
    for when in (ts(0), ts(0), ts(10)):
        c.execute("INSERT INTO chat_log(user_id,created_at) VALUES(?,?)",
                  (USERS["m1@example.com"], when))

    # 白名单：2 在用 / 1 停用；开通申请 1 条未处理
    c.execute("INSERT INTO allowlist(code,name,role,active,added_at) VALUES('2025001','甲','member',1,?)", (ts(5),))
    c.execute("INSERT INTO allowlist(code,name,role,active,added_at) VALUES('2025002','乙','member',1,?)", (ts(5),))
    c.execute("INSERT INTO allowlist(code,name,role,active,added_at) VALUES('2025003','丙','member',0,?)", (ts(5),))
    c.execute("INSERT INTO join_requests(code,name,message,created_at,handled) VALUES('2025009','丁','想加入',?,0)", (ts(1),))
    c.execute("INSERT INTO school_accounts(code,owu_email,owu_password,created_at,last_login) "
              "VALUES('2025001','2025001@stu.wfla-ailab.top','x',?,?)", (ts(5), ts(0)))

    # 流水线：1 共享 / 1 私有；运行记录 1 条近 7 天 + 1 条老的
    c.execute("INSERT INTO pipelines(name,author_id,is_shared,created_at,updated_at) VALUES('共享的',?,1,?,?)",
              (USERS["admin@example.com"], ts(4), ts(4)))
    shared_pid = c.execute("SELECT id FROM pipelines WHERE name='共享的'").fetchone()["id"]
    c.execute("INSERT INTO pipelines(name,author_id,is_shared,created_at,updated_at) VALUES('私有的',?,0,?,?)",
              (USERS["admin@example.com"], ts(4), ts(4)))
    for when in (ts(1), ts(20)):
        c.execute("INSERT INTO pipeline_runs(pipeline_id,user_id,input,stages,created_at) "
                  "VALUES(?,?,'hi','[]',?)", (shared_pid, USERS["m1@example.com"], when))
    c.commit()
    c.close()


seed()


def flat():
    """调用被测的聚合函数，把 groups 拍平成 {标签: 数字}。"""
    with arena.app.app_context():
        groups, todo = arena.collect_stats(arena.db())
    out = {}
    for g in groups:
        for item in g["items"]:
            out[item["label"]] = item["value"]
    return out, todo


def hand(label_sql):
    """直接用 sqlite 手查一个数字（不复用被测代码）。"""
    c = conn()
    value = c.execute(label_sql).fetchone()[0]
    c.close()
    return value


def client_as(email):
    c = arena.app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = USERS[email]
    return c


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
@check("看板要管理员：未登录跳登录页，普通社员被挡回首页")
def t_gate():
    with arena.app.test_client() as c:
        r = c.get("/admin/stats")
        assert r.status_code == 302, r.status_code
        assert "/login" in r.headers.get("Location", ""), r.headers.get("Location")
    r = client_as("m1@example.com").get("/admin/stats")
    assert r.status_code == 302, "普通社员不该看得板"
    assert "/login" not in r.headers.get("Location", ""), "普通社员是被送回首页，不是登录页"


@check("管理员能打开看板，页面上有关键区块")
def t_page_renders():
    body = client_as("admin@example.com").get("/admin/stats").get_data(as_text=True)
    for token in ("数据看板", "需要你处理", "待审 Agent", "站内公告", "白名单在用", "今日试聊"):
        assert token in body, f"看板缺少「{token}」：{body[:300]}"


@check("看板数字 = sqlite 手查（逐项核对）")
def t_numbers_match_hand_query():
    values, todo = flat()
    expected = {
        "待审 Agent": hand("SELECT COUNT(*) FROM agents WHERE status='pending'"),
        "已上架 Agent": hand("SELECT COUNT(*) FROM agents WHERE status='approved'"),
        "已拒绝 Agent": hand("SELECT COUNT(*) FROM agents WHERE status='rejected'"),
        "待审作品": hand("SELECT COUNT(*) FROM projects WHERE status='pending'"),
        "已上架作品": hand("SELECT COUNT(*) FROM projects WHERE status='approved'"),
        "平台用户": hand("SELECT COUNT(*) FROM users"),
        "白名单在用": hand("SELECT COUNT(*) FROM allowlist WHERE active=1"),
        "白名单已停用": hand("SELECT COUNT(*) FROM allowlist WHERE active=0"),
        "待处理开通申请": hand("SELECT COUNT(*) FROM join_requests WHERE handled=0"),
        "学校账号已开通": hand("SELECT COUNT(*) FROM school_accounts"),
        "流水线总数": hand("SELECT COUNT(*) FROM pipelines"),
        "已共享流水线": hand("SELECT COUNT(*) FROM pipelines WHERE is_shared=1"),
        "流水线运行总数": hand("SELECT COUNT(*) FROM pipeline_runs"),
    }
    for label, want in expected.items():
        assert values.get(label) == want, f"{label}: 看板={values.get(label)} 手查={want}"

    # 造数据时故意留了 10 天前/20 天前的记录，用来验证「近 7 天」窗口真的在过滤
    assert values["近 7 天试聊"] == 2, values["近 7 天试聊"]
    assert values["近 7 天流水线运行"] == 1, values["近 7 天流水线运行"]
    assert values["今日试聊"] == 2, values["今日试聊"]

    assert todo == {"agents": 2, "projects": 1, "join": 1}, todo


@check("空库看板不报错，全为 0")
def t_empty_db():
    empty = os.path.join(TMP, "empty.db")
    with arena.app.app_context():
        saved = arena.DB_PATH
        arena.DB_PATH = empty
        try:
            arena.g.pop("db", None)
            groups, todo = arena.collect_stats(arena.db())
        finally:
            arena.DB_PATH = saved
            arena.g.pop("db", None)
    for g in groups:
        for item in g["items"]:
            assert item["value"] == 0, f"空库 {item['label']} 应为 0，实际 {item['value']}"
    assert todo == {"agents": 0, "projects": 0, "join": 0}, todo


@check("待审队列跨表统计正确（含 school_auth 的三张表）")
def t_cross_table():
    c = conn()
    counts = {
        "allowlist": c.execute("SELECT COUNT(*) FROM allowlist").fetchone()[0],
        "join_requests": c.execute("SELECT COUNT(*) FROM join_requests").fetchone()[0],
        "school_accounts": c.execute("SELECT COUNT(*) FROM school_accounts").fetchone()[0],
    }
    c.close()
    assert counts == {"allowlist": 3, "join_requests": 1, "school_accounts": 1}, counts


@check("写公告 → 全站出现横幅（登录页也能看到）")
def t_notice_write_and_show():
    text = "周五活动改到 16:30，机房见"
    r = client_as("admin@example.com").post("/admin/notice", data={"notice": text})
    assert r.status_code == 302, r.status_code

    page = client_as("m1@example.com").get("/").get_data(as_text=True)
    assert "社团公告" in page and text in page, "首页应出现公告横幅"

    with arena.app.test_client() as c:      # 未登录页面（登录页）同样带横幅
        login_page = c.get("/login").get_data(as_text=True)
    assert text in login_page, "登录页也该看到公告"

    c = conn()
    stored = c.execute("SELECT value FROM settings WHERE key='notice'").fetchone()
    c.close()
    assert stored and stored["value"] == text, stored


@check("清空公告 → 横幅消失，settings 里的键被删掉")
def t_notice_clear():
    client_as("admin@example.com").post("/admin/notice", data={"notice": ""})
    page = client_as("m1@example.com").get("/").get_data(as_text=True)
    assert "社团公告" not in page, "公告已清空，不该再有横幅"
    c = conn()
    row = c.execute("SELECT value FROM settings WHERE key='notice'").fetchone()
    c.close()
    assert row is None, "清空应删除该键（而不是存空串）"


@check("公告超长会截断到 500 字")
def t_notice_truncated():
    client_as("admin@example.com").post("/admin/notice", data={"notice": "长" * 800})
    c = conn()
    value = c.execute("SELECT value FROM settings WHERE key='notice'").fetchone()["value"]
    c.close()
    assert len(value) == 500, f"应截断到 500，实际 {len(value)}"
    client_as("admin@example.com").post("/admin/notice", data={"notice": ""})


@check("普通社员不能改公告（改不动，库里不留痕迹）")
def t_notice_admin_only():
    client_as("admin@example.com").post("/admin/notice", data={"notice": "管理员写的"})
    r = client_as("m1@example.com").post("/admin/notice", data={"notice": "社员偷偷改的"})
    assert r.status_code == 302, r.status_code
    c = conn()
    value = c.execute("SELECT value FROM settings WHERE key='notice'").fetchone()["value"]
    c.close()
    assert value == "管理员写的", f"公告被非管理员改掉了：{value}"
    client_as("admin@example.com").post("/admin/notice", data={"notice": ""})


@check("公告不影响其他页面渲染（对 /pipelines、/projects 也安全）")
def t_notice_on_other_pages():
    client_as("admin@example.com").post("/admin/notice", data={"notice": "横幅测试"})
    c = client_as("admin@example.com")
    for path in ("/pipelines", "/projects", "/mine", "/admin/allowlist", "/admin"):
        r = c.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"
        assert "横幅测试" in r.get_data(as_text=True), f"{path} 上没看到公告"
    client_as("admin@example.com").post("/admin/notice", data={"notice": ""})


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

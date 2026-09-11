#!/usr/bin/env python3
"""共享流水线「复制到我的」自测（不联网、不碰生产库）。

验证真正上线的那段逻辑：POST /pipeline/<id>/copy → 深拷贝一条副本，
作者 = 复制者、默认未共享；改副本不影响源；别人的未共享流水线复制不了。

用法：python tests/test_pipeline_copy.py
"""

import os
import re
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
    "ARENA_BASE_PATH": "/",          # 测试直接打 /pipeline/... 路径
    "ARENA_SECRET": "test-secret",
    "WEBUI_ADMIN_EMAIL": "admin@example.com",
    "WEBUI_ADMIN_PASSWORD": "adminpw",
})

import app as arena  # noqa: E402

arena.app.config["TESTING"] = True
arena.app.config["SESSION_COOKIE_SECURE"] = False


def conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def prep_db():
    """建表（arena.db() 必须在 app_context 里调才会建表）并造两个用户。"""
    with arena.app.app_context():
        arena.db()
    c = conn()
    for email, name in (("owner@example.com", "原作者"), ("copier@example.com", "复制者")):
        c.execute("INSERT INTO users(email,name,is_admin,created_at) VALUES(?,?,?,?)",
                  (email, name, 0, "2026-09-10 10:00:00"))
    c.commit()
    ids = {k: c.execute("SELECT id FROM users WHERE email=?", (e,)).fetchone()["id"]
           for k, e in (("owner", "owner@example.com"), ("copier", "copier@example.com"))}
    c.close()
    return ids


INITIAL = prep_db()

STEP_ROWS = [
    (1, "初步生成", "deepseek-chat", "你是内容创作助手。", "生成初稿", "0.7", "2048"),
    (2, "专业评审", "deepseek-reasoner", "你是评审专家。", "挑毛病", "0.5", "4096"),
]


def seed_pipeline(shared=1, author=None, with_agent=False):
    """造一条流水线，返回 (pipeline_id, agent_id or None)。"""
    author = author or INITIAL["owner"]
    c = conn()
    agent_id = None
    if with_agent:
        cur = c.execute(
            "INSERT INTO agents(model_id,name,description,system,model,tag,params,"
            "author_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("temp-agent", "临时 Agent", "", "你是助手。", "deepseek-chat", "学习",
             "{}", author, "approved", "2026-09-10 10:00:00"),
        )
        agent_id = cur.lastrowid
    cur = c.execute(
        "INSERT INTO pipelines(name,description,author_id,is_shared,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?)",
        ("文章生成流水线", "示例三连", author, shared, "2026-09-10 10:00:00", "2026-09-10 10:00:00"),
    )
    pid = cur.lastrowid
    for pos, nm, model, system, instruction, temp, tokens in STEP_ROWS:
        c.execute(
            "INSERT INTO pipeline_steps(pipeline_id,position,agent_id,name,model,system,"
            "instruction,temperature,max_tokens) VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, pos, agent_id if (with_agent and pos == 2) else None,
             nm, model, system, instruction, temp, tokens),
        )
    c.commit()
    c.close()
    return pid, agent_id


def steps_of(pid):
    c = conn()
    rows = [dict(r) for r in c.execute(
        "SELECT position,agent_id,name,model,system,instruction,temperature,max_tokens "
        "FROM pipeline_steps WHERE pipeline_id=? ORDER BY position ASC", (pid,))]
    c.close()
    return rows


def pipelines_count():
    c = conn()
    n = c.execute("SELECT COUNT(*) AS c FROM pipelines").fetchone()["c"]
    c.close()
    return n


def copy_as(uid, pid):
    """以某个用户身份 POST 复制，返回 (response, 新 pipeline_id or None)。"""
    c = arena.app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = uid
    resp = c.post(f"/pipeline/{pid}/copy")
    new_id = None
    loc = resp.headers.get("Location", "")
    m = re.search(r"/pipeline/(\d+)$", loc)
    if m:
        new_id = int(m.group(1))
    return resp, new_id


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
@check("复制别人的共享流水线：深拷贝成我的副本（作者=我、默认未共享）")
def t_copy_shared():
    pid, _ = seed_pipeline(shared=1)
    resp, new_id = copy_as(INITIAL["copier"], pid)
    assert resp.status_code == 302, resp.status_code
    assert new_id and new_id != pid, f"应跳到副本详情页，Location={resp.headers.get('Location')}"

    c = conn()
    row = c.execute("SELECT * FROM pipelines WHERE id=?", (new_id,)).fetchone()
    c.close()
    assert row is not None, "副本应该存在"
    assert row["author_id"] == INITIAL["copier"], "副本作者必须是复制者"
    assert row["is_shared"] == 0, "副本默认不该是共享状态"
    assert row["name"].endswith("（副本）"), row["name"]
    assert row["description"] == "示例三连", "说明应一起拷过来"

    assert steps_of(new_id) == steps_of(pid), "副本步骤应与源完全一致"


@check("改副本不影响源（两边彻底解耦）")
def t_copy_is_independent():
    pid, _ = seed_pipeline(shared=1)
    _, new_id = copy_as(INITIAL["copier"], pid)

    c = conn()
    c.execute("UPDATE pipeline_steps SET instruction='只改副本' WHERE pipeline_id=? AND position=1",
              (new_id,))
    c.execute("UPDATE pipelines SET name='我改过的副本' WHERE id=?", (new_id,))
    c.commit()
    c.close()

    src = steps_of(pid)
    assert src[0]["instruction"] == "生成初稿", f"源步骤被改到了：{src[0]}"
    c = conn()
    src_name = c.execute("SELECT name FROM pipelines WHERE id=?", (pid,)).fetchone()["name"]
    c.close()
    assert src_name == "文章生成流水线", f"源名称被改到了：{src_name}"


@check("别人的未共享流水线：复制不了，也不会凭空建出副本")
def t_cannot_copy_private():
    pid, _ = seed_pipeline(shared=0)
    before = pipelines_count()
    resp, new_id = copy_as(INITIAL["copier"], pid)
    assert resp.status_code == 302, resp.status_code
    assert "/pipelines" in resp.headers.get("Location", ""), \
        f"应被送回流水线列表，Location={resp.headers.get('Location')}"
    assert new_id is None, "不该跳到新副本"
    assert pipelines_count() == before, "不该新建任何流水线"


@check("未登录复制 → 先登录，且不建副本")
def t_copy_requires_login():
    pid, _ = seed_pipeline(shared=1)
    before = pipelines_count()
    with arena.app.test_client() as c:
        resp = c.post(f"/pipeline/{pid}/copy")
    assert resp.status_code == 302, resp.status_code
    assert "/login" in resp.headers.get("Location", ""), resp.headers.get("Location")
    assert pipelines_count() == before


@check("副本保留 Agent 引用（引用的是同一个 Agent，不是复制 Agent）")
def t_copy_keeps_agent_reference():
    pid, agent_id = seed_pipeline(shared=1, with_agent=True)
    assert agent_id, "测试前提：源里第 2 步引用了 Agent"
    _, new_id = copy_as(INITIAL["copier"], pid)
    copied = steps_of(new_id)
    assert copied[1]["agent_id"] == agent_id, f"应保留同一个 agent_id：{copied[1]}"
    # 源里第 1 步是自定义步骤（无 Agent 引用），复制后仍应为空
    assert copied[0]["agent_id"] is None, copied[0]


@check("复制自己的流水线也可以（等于做个备份）")
def t_copy_own():
    pid, _ = seed_pipeline(shared=0)
    resp, new_id = copy_as(INITIAL["owner"], pid)
    assert new_id and new_id != pid, resp.headers.get("Location")
    c = conn()
    row = c.execute("SELECT author_id,is_shared FROM pipelines WHERE id=?", (new_id,)).fetchone()
    c.close()
    assert row["author_id"] == INITIAL["owner"] and row["is_shared"] == 0


@check("详情页有「复制到我的」按钮，指向 copy 路由")
def t_detail_page_has_button():
    pid, _ = seed_pipeline(shared=1)
    c = arena.app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = INITIAL["copier"]
    body = c.get(f"/pipeline/{pid}").get_data(as_text=True)
    assert "复制到我的" in body, body[:400]
    assert f'action="/pipeline/{pid}/copy"' in body, "按钮应 POST 到 copy 路由"


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

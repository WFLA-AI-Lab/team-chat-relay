#!/usr/bin/env python3
"""把本轮新增/改动的运维页面渲染成静态 HTML，供人工或截图复核。

覆盖三块：
  - /admin/stats     数据看板（"需要你处理"卡片 + 三组条形图 + 公告编辑框）
  - /projects        作品页（站内公告横幅 + 每件已上架作品的"复制分享文案"按钮）
  - /pipeline/<id>   流水线详情（"复制到我的"按钮）

用法：
    python tests/render_ops_pages.py             # 渲染到 .verify/render/*.html
    python tests/render_ops_pages.py --shot      # 额外用本机浏览器截图（.verify/render/shot-*.png）
    python tests/render_ops_pages.py --serve     # 额外在本机 8098 起真服务，便于浏览器复核

不联网、不碰生产库：临时 sqlite + 造数据；OpenWebUI 完全不需要。
"""

import os
import sqlite3
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ARENA_DIR = os.path.join(ROOT, "arena")
OUT_DIR = os.path.join(ROOT, ".verify", "render")
sys.path.insert(0, ARENA_DIR)

TMP = tempfile.mkdtemp()
DB_FILE = os.path.join(TMP, "render.db")
os.environ.update({
    "ARENA_DB": DB_FILE,
    "ARENA_BASE_PATH": "/",
    "ARENA_SECRET": "render-secret",
    "WEBUI_ADMIN_EMAIL": "admin@example.com",
})

import app as arena  # noqa: E402
import school_auth  # noqa: E402

arena.app.config["SESSION_COOKIE_SECURE"] = False

NOTICE = ("【9/14 公告】周三 16:30 社团活动改到 B301；本周擂台主题「学习搭子」，"
          "作品截止周五 20:00。")


def seed():
    """造一份看起来像真数据的数据（数字和看板上的条形图对得上）。"""
    with arena.app.app_context():
        arena.db()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    school_auth.ensure_tables(conn)

    now = "2026-09-14 10:00:00"
    conn.execute("INSERT INTO users(email,name,is_admin,created_at) VALUES(?,?,?,?)",
                 ("president@example.com", "社长", 1, "2026-08-01 10:00:00"))
    admin_id = conn.execute("SELECT id FROM users WHERE email='president@example.com'").fetchone()["id"]
    for i, (email, name, ts) in enumerate([
        ("zhang@example.com", "张三", "2026-09-12 09:00:00"),
        ("li@example.com", "李四", "2026-09-10 09:00:00"),
        ("wang@example.com", "王五", "2026-07-01 09:00:00"),
    ]):
        conn.execute("INSERT INTO users(email,name,is_admin,created_at) VALUES(?,?,?,?)",
                     (email, name, 0, ts))
    member_id = conn.execute("SELECT id FROM users WHERE email='zhang@example.com'").fetchone()["id"]

    # agents: 2 已上架 / 1 待审 / 1 已拒
    for mid, name, status in (
        ("article-helper", "文章助手", "approved"),
        ("code-tutor", "代码小老师", "approved"),
        ("study-buddy", "学习搭子", "pending"),
        ("too-loud", "太吵的助手", "rejected"),
    ):
        conn.execute(
            "INSERT INTO agents(model_id,name,description,system,model,tag,params,author_id,"
            "status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (mid, name, name + "的简介", "你是" + name, "deepseek-chat", "学习", "{}",
             member_id, status, now))

    # 白名单：2 在用 / 1 停用；学校账号 1 个；申请 2 待处理 / 1 已处理
    school_auth.add_entries(conn, [("2025001", "张三"), ("2025002", "李四")], now, note="高一")
    school_auth.add_entries(conn, [("2024001", "老社员")], now)
    school_auth.set_active(conn, "2024001", False)
    conn.execute("INSERT INTO school_accounts(code,owu_email,owu_password,created_at) "
                 "VALUES('2025001','2025001@stu.wfla-ailab.top','pw','2026-09-05 09:00:00')")
    school_auth.record_request(conn, "2025099", "赵六", "高一3班，想参加周三活动", now)
    school_auth.record_request(conn, "2025098", "钱七", "想学做智能体", now)
    school_auth.record_request(conn, "2025097", "孙八", "已加群", now)
    conn.execute("UPDATE join_requests SET handled=1 WHERE code='2025097'")

    # 用量：今日试聊 3 / 近 7 天 5
    for ts in ("2026-09-14 09:01:00", "2026-09-14 09:02:00", "2026-09-14 09:03:00",
               "2026-09-12 09:00:00", "2026-09-10 09:00:00"):
        conn.execute("INSERT INTO chat_log(user_id,created_at) VALUES(?,?)", (member_id, ts))

    # 作品：2 已上架 / 1 待审
    for name, owner, repo, status in (
        ("文章助手", "WFLA-AI-Lab", "team-chat-relay", "approved"),
        ("运动会计时器", "WFLA-AI-Lab", "sports-timer", "approved"),
        ("还没审的作品", "someone", "draft-repo", "pending"),
    ):
        conn.execute(
            "INSERT INTO projects(user_id,repo_url,demo_url,display_name,description,tag,"
            "github_owner,github_repo,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (member_id, f"https://github.com/{owner}/{repo}", "", name,
             name + "的简介", "学习", owner, repo, status, now))

    # 流水线：1 条自己的（3 步）+ 1 条共享的；运行 4 次（近 7 天 3 次）
    cur = conn.execute("INSERT INTO pipelines(name,description,author_id,is_shared,created_at,updated_at) "
                       "VALUES('作文精修流水线','三段式改作文',?,0,?,?)", (member_id, now, now))
    pid = cur.lastrowid
    for pos, (sname, inst) in enumerate([
        ("找问题", "找出这篇文章最大的三个问题，逐条列出"),
        ("提建议", "针对每个问题给出具体改法，要能直接照做"),
        ("改一版", "按上面的建议重写一版，保持作者原意"),
    ]):
        conn.execute("INSERT INTO pipeline_steps(pipeline_id,position,name,model,system,instruction,"
                     "temperature,max_tokens) VALUES(?,?,?,?,?,?,?,?)",
                     (pid, pos, sname, "deepseek-chat", "你是语文老师", inst, "0.7", "2048"))
    conn.execute("INSERT INTO pipelines(name,description,author_id,is_shared,created_at,updated_at) "
                 "VALUES('物理题讲解','共享给全社',?,1,?,?)", (admin_id, now, now))
    for i in range(4):
        conn.execute("INSERT INTO pipeline_runs(pipeline_id,user_id,input,stages,created_at) "
                     "VALUES(?,?,?,?,?)", (pid, member_id, "输入" + str(i), "[]",
                                           "2026-09-1%d 09:00:00" % (i + 1)))

    conn.execute("INSERT INTO settings(key,value) VALUES('notice',?)", (NOTICE,))
    conn.commit()
    conn.close()
    return admin_id, member_id, pid


def render():
    os.makedirs(OUT_DIR, exist_ok=True)
    admin_id, member_id, pid = seed()
    written = []

    def dump(html, name):
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        written.append(path)

    # 1) 看板（管理员）
    with arena.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["uid"] = admin_id
        html = c.get("/admin/stats").get_data(as_text=True)
        assert "需要你处理" in html, "看板没渲染出来"
        assert "notice" in html or "公告" in html, "公告编辑区没渲染出来"
        dump(html, "admin-stats.html")

        # 2) 流水线详情（作者本人 → 有"复制到我的"）
        html = c.get(f"/pipeline/{pid}").get_data(as_text=True)
        assert "复制到我的" in html, "流水线详情页没渲染出复制按钮"
        dump(html, "pipeline-detail.html")

    # 3) 作品页（普通社员）：公告横幅 + 分享按钮
    with arena.app.test_client() as c2:
        with c2.session_transaction() as sess:
            sess["uid"] = member_id
        html = c2.get("/projects").get_data(as_text=True)
        assert "复制分享文案" in html, "作品页没渲染出分享按钮"
        assert NOTICE[:12] in html, "作品页没渲染出公告横幅"
        dump(html, "projects-share.html")

    # 4) 无公告时横幅必须消失（避免空橙条）
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM settings WHERE key='notice'")
    conn.commit()
    conn.close()
    with arena.app.test_client() as c3:
        with c3.session_transaction() as sess:
            sess["uid"] = member_id
        html = c3.get("/projects").get_data(as_text=True)
        assert NOTICE[:12] not in html, "清空公告后横幅还在"
        dump(html, "projects-no-notice.html")

    for p in written:
        print("wrote", p, os.path.getsize(p), "bytes")
    return written


if __name__ == "__main__":
    written = render()
    if "--shot" in sys.argv:
        from render_shot import shot

        for page in written:
            shot(page)
    if "--serve" in sys.argv:
        print("serving http://127.0.0.1:8098/admin/stats （Ctrl+C 结束；管理员已登录）")
        admin_id, member_id, pid = seed()
        arena.app.secret_key = "render-secret"

        @arena.app.before_request
        def _auto_login():
            from flask import session
            session["uid"] = admin_id

        threading.Thread(target=lambda: arena.app.run(host="127.0.0.1", port=8098),
                         daemon=True).start()
        threading.Event().wait()

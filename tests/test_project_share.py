#!/usr/bin/env python3
"""作品一键分享 自测（不联网、不碰生产）。

关键契约是**跨仓库**的：擂台作品页复制出来的分享文案里带的锚点
（#pc-owner-repo）必须正好是社团网站 showcase.html 真正生成的卡片 id，
否则"链接直达"是假的。所以这个测试会同时读两个仓库的文件来对齐。

另外验证：作品名里的引号/尖括号不会把 data-share 属性撑破（别引入 XSS）。

用法：python tests/test_project_share.py
"""

import html
import os
import re
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ARENA_DIR = os.path.join(ROOT, "arena")
WEBSITE_SHOWCASE = os.path.normpath(os.path.join(ROOT, "..", "Website", "showcase.html"))
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

with arena.app.app_context():
    arena.db()          # 建表

TRICKY_NAME = 'Quote" and <b>tag</b>'

conn = sqlite3.connect(DB_FILE)
conn.row_factory = sqlite3.Row
conn.execute("INSERT INTO users(email,name,is_admin,created_at) VALUES(?,?,?,?)",
             ("m@example.com", "社员", 0, "2026-09-10 10:00:00"))
uid = conn.execute("SELECT id FROM users WHERE email='m@example.com'").fetchone()["id"]
for name, owner, repo, status in (
    ("文章助手", "WFLA-AI-Lab", "team-chat-relay", "approved"),
    (TRICKY_NAME, "someone", "cool-repo", "approved"),
    ("还没审的", "someone", "pending-repo", "pending"),
):
    conn.execute(
        "INSERT INTO projects(user_id,repo_url,demo_url,display_name,description,tag,"
        "github_owner,github_repo,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (uid, f"https://github.com/{owner}/{repo}", "", name, "简介", "学习",
         owner, repo, status, "2026-09-10 10:00:00"),
    )
conn.commit()
conn.close()


def projects_page():
    c = arena.app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = uid
    return c.get("/projects").get_data(as_text=True)


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
@check("已上架作品都有「复制分享文案」按钮，且带 data-share")
def t_buttons_present():
    body = projects_page()
    assert "复制分享文案" in body, body[:400]
    assert body.count("data-share=") >= 2, "两个已上架作品都应有分享按钮"


@check("分享文案含：作品名 + 仓库链接 + 官网作品廊链接")
def t_share_text_content():
    body = projects_page()
    m = re.search(r'data-share="([^"]*)"', body)
    assert m, "找不到 data-share 属性"
    text = html.unescape(m.group(1))
    assert "文章助手" in text, text
    assert "https://github.com/WFLA-AI-Lab/team-chat-relay" in text, text
    assert "https://wfla-ailab.top/showcase.html" in text, text
    assert "（学习）" in text, "带上分类更利于转发：" + text


@check("锚点 = #pc-<owner>-<repo>，且这个 id 确实是 showcase.html 会生成的")
def t_anchor_matches_showcase():
    body = projects_page()
    anchors = set(re.findall(r"showcase\.html#(pc-[\w.-]+)", body))
    assert anchors == {"pc-WFLA-AI-Lab-team-chat-relay", "pc-someone-cool-repo"}, anchors

    assert os.path.exists(WEBSITE_SHOWCASE), f"找不到 {WEBSITE_SHOWCASE}"
    with open(WEBSITE_SHOWCASE, "r", encoding="utf-8") as fh:
        showcase = fh.read()
    # showcase.html 里卡片 id 必须由 owner/repo 拼成 pc-<owner>-<repo>，否则锚点指向空气
    assert "id=\"pc-' + esc(owner) + \"-\" + esc(repo)" in showcase, \
        "showcase.html 里找不到 pc-<owner>-<repo> 这种卡片 id 构造"
    assert "focusFromHash" in showcase, "showcase.html 应支持按锚点定位/高亮"


@check("成员分组也加了锚点（#member-<owner>）")
def t_member_anchor():
    with open(WEBSITE_SHOWCASE, "r", encoding="utf-8") as fh:
        showcase = fh.read()
    assert "member-group" in showcase and 'id="member-' in showcase, \
        "每个成员分组应有 #member-<owner> 锚点"


@check("作品名里的引号/尖括号不会撑破属性（防注入）")
def t_escaping():
    body = projects_page()
    raw = re.findall(r'data-share="([^"]*)"', body)
    assert len(raw) == 2, f"两个已上架作品各应有一条完整 data-share：{raw}"
    for value in raw:
        assert "<b>" not in value, f"尖括号必须转义，不能被当 HTML 解析：{value}"
        assert '"' not in value, f"属性值里出现裸双引号会撑破属性：{value}"
    # 带引号/标签的作品名必须原样（转义后）出现在文案里，而不是被截断
    texts = [html.unescape(t) for t in raw]
    assert any(TRICKY_NAME in t for t in texts), f"带引号的作品名应完整出现在文案里：{texts}"
    # 实体形式取决于转义器（MarkupSafe 用 &#34;，html.escape 用 &quot;），两者都算合格；
    # 浏览器读 dataset.share 时会把实体解回真字符，所以剪贴板里仍是原名。
    assert ("&#34;" in body or "&quot;" in body), "双引号应被转义成实体"
    assert "&lt;b&gt;" in body, "尖括号应被转义成实体"


@check("待审作品没有分享按钮（只分享已上架的）")
def t_pending_not_shared():
    body = projects_page()
    pending_anchor = "pc-someone-pending-repo"
    assert pending_anchor not in body, "未审核通过的作品不该出现在分享文案里"


@check("分享脚本用的是剪贴板 API + execCommand 兜底（http 下也能复制）")
def t_clipboard_fallback():
    body = projects_page()
    assert "navigator.clipboard" in body, "优先用剪贴板 API"
    assert "execCommand('copy')" in body, "非 https / 老浏览器要能退回 execCommand"
    assert "js-share" in body, "按钮要带 js-share 类，事件委托靠它"


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

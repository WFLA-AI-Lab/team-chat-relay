#!/usr/bin/env python3
"""教程站站内公告（_notice.md）自测（不联网、不碰生产）。

守两件事：
  1. content/_notice.md 有内容 → 所有页面顶部出现横幅（markdown 渲染）；
     没有文件 / 空白 → 不出现横幅；
  2. 下划线开头的文件是内部文件，不能被当成文章访问（/_notice 必须 404）。

用法：python tests/test_tutorial_notice.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
TUTORIAL_DIR = os.path.join(ROOT, "tutorial")
sys.path.insert(0, TUTORIAL_DIR)

os.environ.setdefault("CHAT_DOMAIN", "chat.wfla-ailab.top")
os.environ.setdefault("OPENWEBUI_URL", "http://stub-openwebui:8080")
os.environ.setdefault("COOKIE_DOMAIN", "")

import app as tut  # noqa: E402

TMP = tempfile.mkdtemp()
NOTICE = os.path.join(TMP, "_notice.md")
# 让被测代码读临时文件，测试全程不动仓库里的 tutorial/content/
tut.NOTICE_FILE = NOTICE


def write_notice(text):
    with open(NOTICE, "w", encoding="utf-8") as fh:
        fh.write(text)


def remove_notice():
    if os.path.exists(NOTICE):
        os.remove(NOTICE)


def page(path="/"):
    with tut.app.test_client() as c:
        return c.get(path).get_data(as_text=True)


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
@check("没有 _notice.md → 页面不出现横幅")
def t_no_notice_file():
    remove_notice()
    assert tut.read_notice() == "", "没有文件时 read_notice 应返回空串"
    for path in ("/", "/00-ai-agent"):
        body = page(path)
        assert "社团公告" not in body, f"{path} 不该有横幅"


@check("文件只有空白 → 也不算公告")
def t_blank_notice():
    write_notice("\n   \n\t\n")
    assert tut.read_notice() == "", "空白内容不该渲染成横幅"
    assert "社团公告" not in page("/")
    remove_notice()


@check("有公告 → 首页与文章页顶部都出现横幅，内容原样显示")
def t_notice_shows():
    write_notice("本周五 16:30 机房活动，主题「提示词工程」。")
    try:
        for path in ("/", "/00-ai-agent"):
            body = page(path)
            assert "社团公告" in body, f"{path} 应出现公告横幅"
            assert "提示词工程" in body, f"{path} 横幅里没有公告正文"
    finally:
        remove_notice()


@check("公告支持 markdown（加粗与链接）")
def t_markdown_rendered():
    write_notice("**重要**：请看[活动页](https://wfla-ailab.top/activities.html)")
    try:
        body = page("/")
        assert "<strong>重要</strong>" in body or "<b>重要</b>" in body, body[:400]
        assert 'href="https://wfla-ailab.top/activities.html"' in body, "链接应渲染成 <a>"
    finally:
        remove_notice()


@check("frontmatter 只做配置，不进横幅正文")
def t_frontmatter_stripped():
    write_notice("---\ntitle: 内部标题\n---\n真正的公告内容")
    try:
        body = page("/")
        assert "真正的公告内容" in body, "正文应该显示"
        assert "内部标题" not in body, "frontmatter 不该出现在页面上"
    finally:
        remove_notice()


@check("下划线开头的文件不是文章：/_notice 与 /_任意 都 404")
def t_underscore_not_served():
    write_notice("公告内容")
    probe = os.path.join(tut.CONTENT, "_probe_internal.md")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("这是内部文件，不该被当成文章\n")
    try:
        with tut.app.test_client() as c:
            for path in ("/_notice", "/_probe_internal"):
                r = c.get(path)
                assert r.status_code == 404, f"{path} 应 404，实际 {r.status_code}"
    finally:
        os.remove(probe)
        remove_notice()


@check("_notice.md 不会出现在首页文章列表里")
def t_notice_not_listed():
    write_notice("公告内容")
    try:
        body = page("/")
        assert "_notice" not in body, "公告文件不该被列成一篇文章"
    finally:
        remove_notice()


@check("公告为空时其它页面照常 200（不会因为读公告而挂掉）")
def t_pages_ok_without_notice():
    remove_notice()
    for path in ("/", "/login", "/00-ai-agent", "/07-glossary"):
        with tut.app.test_client() as c:
            r = c.get(path)
            assert r.status_code == 200, f"{path} → {r.status_code}"


def main():
    failed = 0
    try:
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
    finally:
        remove_notice()
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

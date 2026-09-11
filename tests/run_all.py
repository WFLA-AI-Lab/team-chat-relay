#!/usr/bin/env python3
"""一键跑完全部离线自检，并且**不丢失败原因**。

为什么需要它：11 个测试文件各自独立、都要单独敲命令，手写 for 循环又很容易
只留最后一行（"11/12 通过"）——**把究竟是哪一项挂了、为什么挂，全丢了**。
这个 runner 把每个文件的失败明细原样打印出来。

用法：
    python tests/run_all.py              # 跑 tests/test_*.py
    python tests/run_all.py --checks     # 顺带渲染页面 + 跑布局/像素体检（需要本机浏览器）
    python tests/run_all.py --verbose    # 连通过的文件也打印完整输出

退出码：只要有任何一个文件失败就是 1（可直接给 CI 用）。
Windows 上不用自己 export PYTHONIOENCODING —— 子进程环境在本脚本里设好了。
"""

import os
import re
import subprocess
import sys

# 本脚本自己也打印中文：Windows 下默认 stdout 是 GBK，不改成 UTF-8 就会乱码。
# 改好之后用户不需要自己 export PYTHONIOENCODING（子进程环境在 child_env() 里另设）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SUMMARY_RE = re.compile(r"\d+\s*/\s*\d+\s*通过|PASS\s+\S|FAIL|ERROR")
# 优先挑"总结行"（N/M 通过），挑不到再退回到最后一条 PASS/FAIL
SUMMARY_PREF = re.compile(r"\d+\s*/\s*\d+.*通过|全绿")

# --checks 时额外跑的"渲染 -> 体检"链（顺序有意义：先出 HTML，再量）
CHECK_CHAIN = [
    ("渲染运维页面", ["render_ops_pages.py", "--shot"]),
    ("渲染分享落地页", ["render_share_landing.py", "--shot"]),
    ("布局体检（5 页面）", ["check_rendered_layout.py",
                            "--html", ".verify/render/admin-stats.html",
                            "--html", ".verify/render/pipeline-detail.html",
                            "--html", ".verify/render/projects-share.html",
                            "--html", ".verify/render/projects-no-notice.html",
                            "--html", ".verify/render/showcase-harness.html"]),
    ("像素体检", ["check_pixels.py"]),
]


def child_env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run(script, args=()):
    """跑一个 tests/ 下的脚本，返回 (ok, 输出, 摘要行)。"""
    cmd = [sys.executable, os.path.join(HERE, script)] + list(args)
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, env=child_env())
    out = (proc.stdout or b"").decode("utf-8", "replace") \
        + (proc.stderr or b"").decode("utf-8", "replace")
    summary = ""
    lines = out.splitlines()
    for line in reversed(lines):
        if SUMMARY_PREF.search(line):
            summary = line.strip()
            break
    if not summary:
        for line in reversed(lines):
            if SUMMARY_RE.search(line):
                summary = line.strip()
                break
    return proc.returncode == 0, out, summary


def print_failure_detail(out):
    """只打印失败/报错的行（带上下文），其它噪声丢掉。"""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"\s*(FAIL|ERROR)\b", line):
            print("        " + line.strip())
            # 失败行下面通常跟着原因（缩进更深的断言信息）
            for extra in lines[i + 1:i + 4]:
                if re.match(r"\s{6,}\S", extra) and not re.match(r"\s*(PASS|FAIL|ERROR)\b", extra):
                    print("        " + extra.strip())
    for line in lines:
        if "Traceback" in line or line.startswith("AssertionError"):
            print("        " + line.strip())


def main():
    verbose = "--verbose" in sys.argv
    with_checks = "--checks" in sys.argv

    files = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    print("== 离线测试：%d 个文件 ==" % len(files))
    failed = []
    for name in files:
        ok, out, summary = run(name)
        print("%s %-28s %s" % ("OK  " if ok else "FAIL", name, summary or ""))
        if not ok:
            failed.append(name)
            print_failure_detail(out)
        elif verbose:
            print(out.rstrip())

    if with_checks:
        print("\n== 渲染 + 体检链 ==")
        for label, args in CHECK_CHAIN:
            ok, out, summary = run(args[0], args[1:])
            print("%s %-20s %s" % ("OK  " if ok else "FAIL", label, summary or ""))
            if not ok:
                failed.append(label)
                print(out.rstrip())

    print("\n%d/%d 个文件通过" % (len(files) - len([f for f in failed if f in files]), len(files)))
    if failed:
        print("失败清单：")
        for name in failed:
            print("  - " + name)
        return 1
    print("全绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

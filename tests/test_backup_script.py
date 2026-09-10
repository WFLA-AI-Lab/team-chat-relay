#!/usr/bin/env python3
"""验证 scripts/backup.sh 的擂台备份逻辑（本机可跑，不需要 docker）。

测的是脚本里真正嵌进去的那段 Python（从 backup.sh 里抽出来执行），不是另写一份：
    1. sh 语法检查
    2. sqlite 在线热备能拿到完整数据
    3. 备份完的 integrity_check 为 ok
    4. 故意弄坏备份 → 完整性检查必须发现（证明检查有牙）
    5. 脚本里确实覆盖了四种产物 + 校验和清单

用法：python tests/test_backup_script.py
"""

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SCRIPT = os.path.join(ROOT, "scripts", "backup.sh")

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def read_script():
    with open(SCRIPT, "r", encoding="utf-8") as fh:
        return fh.read()


def extract_snippet(text):
    """把 <<'PY' ... PY 之间的那段 Python 原样抽出来。"""
    marker = "<<'PY'"
    start = text.index(marker) + len(marker)
    end = text.index("\nPY\n", start)
    return text[start:end].lstrip("\n")


def make_fake_arena_db(path, rows=3):
    """造一个结构类似 arena 的库：带表、带数据、带一个 WAL 之外的索引。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, name TEXT);
        CREATE TABLE allowlist(code TEXT PRIMARY KEY, name TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE agents(id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT UNIQUE, name TEXT);
        """
    )
    for i in range(rows):
        conn.execute("INSERT INTO users(email,name) VALUES(?,?)", (f"u{i}@x.top", f"用户{i}"))
        conn.execute("INSERT INTO allowlist(code,name) VALUES(?,?)", (f"202500{i}", f"社员{i}"))
        conn.execute("INSERT INTO agents(model_id,name) VALUES(?,?)", (f"m{i}", f"Agent{i}"))
    conn.commit()
    conn.close()


def run_snippet(snippet, src_db, dst_db):
    """按 backup.sh 的方式跑：环境变量 ARENA_DB / ARENA_TMP 传参。

    显式要求子进程用 UTF-8 输出（否则 Windows 下按 GBK 输出、本机解码就乱），
    并以 UTF-8 解码，避免中文日志把测试本身搞挂。
    """
    tmp_py = os.path.join(tempfile.mkdtemp(), "snippet.py")
    with open(tmp_py, "w", encoding="utf-8") as fh:
        fh.write(snippet)
    env = dict(os.environ, ARENA_DB=src_db, ARENA_TMP=dst_db,
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([sys.executable, tmp_py], capture_output=True,
                          encoding="utf-8", errors="replace", env=env)


def integrity(db):
    """返回 integrity_check 的文本；库被破坏到打不开也算"检出"（返回 ERROR:...）。"""
    try:
        conn = sqlite3.connect(db)
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return "ERROR:" + type(exc).__name__


@check("sh 语法检查通过（POSIX sh，不含 bash 专有语法）")
def t_syntax():
    d = shutil.which("sh")
    assert d, "找不到 sh"
    r = subprocess.run([d, "-n", SCRIPT], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@check("脚本覆盖四类产物 + 校验和清单 + 7 天保留")
def t_products():
    text = read_script()
    for pattern in ("arena-$STAMP.db", "env-$STAMP.env", "filters-$STAMP.json",
                    "openwebui-$STAMP.sql", "MANIFEST-$STAMP.sha256"):
        assert pattern in text, f"脚本里没有 {pattern}"
    assert "sha256sum -c" in text, "缺少备份后立即校验"
    assert text.count("-mtime +7 -delete") >= 5, "保留策略没覆盖全部产物"
    assert "arena_data" in text, "应说明擂台数据在独立卷里（防后人又改回只备 postgres）"


@check("擂台热备拿到完整数据（表与行数与源库一致）")
def t_hot_backup():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "arena.db")
    dst = os.path.join(tmp, "arena-backup.db")
    make_fake_arena_db(src, rows=3)
    r = run_snippet(extract_snippet(read_script()), src, dst)
    assert r.returncode == 0, f"热备脚本失败：{r.stdout} {r.stderr}"
    assert os.path.exists(dst), "没有产出备份文件"
    assert "integrity_check=ok" in r.stdout, r.stdout

    for table, expect in (("users", 3), ("allowlist", 3), ("agents", 3)):
        conn = sqlite3.connect(dst)
        got = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        assert got == expect, f"{table} 行数不对：{got} != {expect}"


@check("备份文件本身通过 integrity_check")
def t_integrity_ok():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "arena.db")
    dst = os.path.join(tmp, "arena-backup.db")
    make_fake_arena_db(src)
    run_snippet(extract_snippet(read_script()), src, dst)
    assert integrity(dst) == "ok", integrity(dst)


@check("备份损坏时必须被发现（证明检查有牙）")
def t_corruption_detected():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "arena.db")
    dst = os.path.join(tmp, "arena-backup.db")
    make_fake_arena_db(src, rows=50)
    run_snippet(extract_snippet(read_script()), src, dst)
    assert integrity(dst) == "ok", "健康备份应报 ok"

    # 场景一：半截拷贝（文件被截断）
    truncated = os.path.join(tmp, "truncated.db")
    shutil.copyfile(dst, truncated)
    with open(truncated, "r+b") as fh:
        fh.truncate(os.path.getsize(truncated) // 3)
    assert integrity(truncated) != "ok", "被截断的库竟然还报 ok"

    # 场景二：文件头被写坏（模拟磁盘损坏）
    broken = os.path.join(tmp, "broken.db")
    shutil.copyfile(dst, broken)
    with open(broken, "r+b") as fh:
        fh.write(b"\x00" * 64)
    assert integrity(broken) != "ok", "文件头损坏的库竟然还报 ok"


@check("源库不存在时明确报错并返回非 0")
def t_missing_source():
    tmp = tempfile.mkdtemp()
    r = run_snippet(extract_snippet(read_script()),
                    os.path.join(tmp, "nope.db"), os.path.join(tmp, "out.db"))
    assert r.returncode != 0, "源库不存在竟然算成功"
    assert "找不到擂台数据库" in r.stderr, r.stderr


@check("热备不破坏源库（源库仍可读、数据不变）")
def t_source_intact():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "arena.db")
    dst = os.path.join(tmp, "arena-backup.db")
    make_fake_arena_db(src, rows=5)
    before = os.path.getsize(src)
    run_snippet(extract_snippet(read_script()), src, dst)
    assert integrity(src) == "ok", "源库被搞坏了"
    conn = sqlite3.connect(src)
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    assert n == 5, n
    assert os.path.getsize(src) >= before - 4096


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

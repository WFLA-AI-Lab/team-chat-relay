#!/usr/bin/env python3
"""validate.sh --health 自测：用假 docker / 假 curl 驱动体检逻辑，不联网、不碰真容器。

做法：把真实的 scripts/validate.sh 复制进临时仓库目录，PATH 前置一组假
docker / curl，然后跑 `--health`，断言每一段的输出与退出码。

故障是用环境变量模拟的（假 docker 读它们）：
    FAKE_STOPPED=arena        某个容器没起来
    FAKE_PG_READY=0           postgres 连不上
    FAKE_PG_TABLES=0          postgres 里没有表
    FAKE_ARENA_INTEGRITY=坏   擂台库 integrity_check 不 ok
    FAKE_QUEUE=               待审队列读不到
    FAKE_BAD_URL=portal       指定 URL 返回非 200
    FAKE_SITE_CODE=500        所有站点都 500

用法：python tests/test_validate_health.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
REAL_VALIDATE = os.path.join(REPO, "scripts", "validate.sh")
REAL_AGENTS_JSON = os.path.join(REPO, "config", "agents.json")

TMP_ROOT = tempfile.mkdtemp()

DOCKER_SHIM = """#!/usr/bin/env sh
# Fake docker for scripts/validate.sh --health tests. ASCII only on purpose.
ARGS="$*"
case "$ARGS" in
  *"config --quiet"*) exit 0 ;;
  *"config --services"*)
    for svc in postgres open-webui caddy tutorial portal arena; do
      [ "$svc" = "${FAKE_HIDDEN_SERVICE:-}" ] && continue
      echo "$svc"
    done
    exit 0 ;;
  *"ps --status running --services"*)
    for svc in postgres open-webui caddy tutorial portal arena; do
      [ "$svc" = "${FAKE_HIDDEN_SERVICE:-}" ] && continue
      [ "$svc" = "${FAKE_STOPPED:-}" ] && continue
      echo "$svc"
    done
    exit 0 ;;
  *"exec -T postgres pg_isready"*)
    if [ "${FAKE_PG_READY:-1}" = "1" ]; then exit 0; fi
    exit 1 ;;
  *"exec -T postgres psql"*)
    echo "${FAKE_PG_TABLES:-42}"
    exit 0 ;;
  *"exec -T arena python -c"*)
    echo "${FAKE_ARENA_INTEGRITY:-ok}"
    exit 0 ;;
  *"exec -T arena python -"*)
    cat >/dev/null 2>&1 || true
    if [ -n "${FAKE_QUEUE+x}" ]; then
      printf '%s\\n' "$FAKE_QUEUE"
    else
      echo "pending_agents=2 pending_projects=1 join_requests=0 users=5"
    fi
    exit 0 ;;
  *)
    echo "fake docker: unexpected call: $ARGS" >&2
    exit 9 ;;
esac
"""

CURL_SHIM = """#!/usr/bin/env sh
# Fake curl: prints an http status code, nothing else.
last=""
for a in "$@"; do last="$a"; done
if [ -n "${FAKE_BAD_URL:-}" ]; then
  case "$last" in
    *"$FAKE_BAD_URL"*) echo "${FAKE_BAD_CODE:-500}"; exit 0 ;;
  esac
fi
echo "${FAKE_SITE_CODE:-200}"
exit 0
"""


def to_posix(path):
    """C:\\Users\\x → /c/Users/x（给 Git Bash 的 PATH 用）。"""
    p = os.path.abspath(path).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


BIN = os.path.join(TMP_ROOT, "bin")
os.makedirs(BIN)
for name, body in (("docker", DOCKER_SHIM), ("curl", CURL_SHIM)):
    path = os.path.join(BIN, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


def make_repo(manifest_age_hours=0, arena_db=True, check_balance=False):
    """搭一个临时仓库：真实 validate.sh + .env + runtime/backups/ 若干备份文件。"""
    repo = tempfile.mkdtemp(dir=TMP_ROOT)
    os.makedirs(os.path.join(repo, "scripts"))
    os.makedirs(os.path.join(repo, "config"))
    os.makedirs(os.path.join(repo, "runtime", "backups"))
    shutil.copy(REAL_VALIDATE, os.path.join(repo, "scripts", "validate.sh"))
    shutil.copy(REAL_AGENTS_JSON, os.path.join(repo, "config", "agents.json"))
    with open(os.path.join(repo, ".env"), "w", encoding="utf-8") as fh:
        fh.write("CHAT_DOMAIN=chat.wfla-ailab.top\nTUTORIAL_DOMAIN=tutorial.wfla-ailab.top\n")
    with open(os.path.join(repo, ".env.example"), "w", encoding="utf-8") as fh:
        fh.write("CHAT_DOMAIN=chat.wfla-ailab.top\n")

    backups = os.path.join(repo, "runtime", "backups")
    manifest = os.path.join(backups, "MANIFEST-20260910-120000.sha256")
    with open(manifest, "w", encoding="utf-8") as fh:
        fh.write("deadbeef  openwebui-20260910-120000.sql\n")
    when = time.time() - manifest_age_hours * 3600
    os.utime(manifest, (when, when))
    if arena_db:
        with open(os.path.join(backups, "arena-20260910-120000.db"), "w", encoding="utf-8") as fh:
            fh.write("SQLite format 3\x00")
    if check_balance:
        cb = os.path.join(repo, "scripts", "check-balance.sh")
        shutil.copy(os.path.join(REPO, "scripts", "check-balance.sh"), cb)
        os.chmod(cb, 0o755)
    return repo


def run(repo, args=("--health",), **fake_env):
    env = dict(os.environ)
    env.pop("FAKE_STOPPED", None)      # 默认不模拟故障
    env.update({k: str(v) for k, v in fake_env.items()})
    cmd = 'PATH=%s:"$PATH" sh scripts/validate.sh %s' % (to_posix(BIN), " ".join(args))
    return subprocess.run(["bash", "-c", cmd], cwd=repo, env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
@check("一切正常 → 六段全 PASS，退出码 0")
def t_all_green():
    p = run(make_repo())
    assert p.returncode == 0, f"应退出 0，实际 {p.returncode}\n{p.stdout}\n{p.stderr}"
    for section in ("1/6 容器状态", "2/6 站点可达", "3/6 数据库完整性",
                    "4/6 备份新鲜度", "5/6 待审队列", "6/6 API 余额"):
        assert section in p.stdout, f"缺少段落「{section}」\n{p.stdout}"
    assert "FAIL" not in p.stdout, f"不该有 FAIL\n{p.stdout}"
    assert "健康检查通过" in p.stdout, p.stdout


@check("arena 容器没起来 → FAIL 且退出码 1")
def t_container_down():
    p = run(make_repo(), FAKE_STOPPED="arena")
    assert p.returncode == 1, f"应退出 1，实际 {p.returncode}\n{p.stdout}"
    assert "容器 arena 没在运行" in p.stdout, p.stdout


@check("备份超过阈值 → FAIL（默认 48 小时）")
def t_backup_stale():
    p = run(make_repo(manifest_age_hours=120))
    assert p.returncode == 1, p.stdout
    assert "已超过 48 小时" in p.stdout, p.stdout


@check("备份阈值可配：同一份 2 小时前的备份，阈值 1 小时 FAIL、72 小时 PASS")
def t_backup_threshold_configurable():
    repo = make_repo(manifest_age_hours=2)
    tight = run(repo, BACKUP_MAX_AGE_HOURS=1)
    assert tight.returncode == 1 and "已超过 1 小时" in tight.stdout, tight.stdout
    loose = run(repo, BACKUP_MAX_AGE_HOURS=72)
    assert "最新备份在 72 小时内" in loose.stdout, loose.stdout


@check("没有 MANIFEST → FAIL（备份从没跑过）")
def t_no_manifest():
    repo = make_repo()
    os.remove(os.path.join(repo, "runtime", "backups", "MANIFEST-20260910-120000.sha256"))
    p = run(repo)
    assert p.returncode == 1, p.stdout
    assert "没有 MANIFEST" in p.stdout, p.stdout


@check("没有 arena-*.db → FAIL（擂台库没进备份）")
def t_no_arena_db():
    p = run(make_repo(arena_db=False))
    assert p.returncode == 1, p.stdout
    assert "没有 arena-*.db" in p.stdout, p.stdout


@check("站点非 200 → 报出具体是哪一站")
def t_site_bad():
    p = run(make_repo(), FAKE_BAD_URL="portal")
    assert p.returncode == 1, p.stdout
    assert "门户页 → 500" in p.stdout, p.stdout
    assert "聊天站首页 → 200" in p.stdout, "没坏的那几站仍应 PASS"


@check("擂台库 integrity_check 不 ok → FAIL")
def t_db_corrupt():
    p = run(make_repo(), FAKE_ARENA_INTEGRITY="*** in database main ***")
    assert p.returncode == 1, p.stdout
    assert "integrity_check" in p.stdout and "FAIL" in p.stdout, p.stdout


@check("postgres 连不上 / 查不到表 → FAIL")
def t_pg_down():
    p = run(make_repo(), FAKE_PG_READY=0)
    assert p.returncode == 1, p.stdout
    assert "postgres 连不上" in p.stdout, p.stdout

    p2 = run(make_repo(), FAKE_PG_TABLES=0)
    assert p2.returncode == 1, p2.stdout
    assert "库是空的" in p2.stdout, p2.stdout


@check("待审队列读不到 → FAIL（而不是默默显示 0）")
def t_queue_unreadable():
    p = run(make_repo(), FAKE_QUEUE="")
    assert p.returncode == 1, p.stdout
    assert "读不到待审队列" in p.stdout, p.stdout


@check("待审队列读得到 → 原样展示（含各表数量）")
def t_queue_shown():
    p = run(make_repo())
    assert "pending_agents=2" in p.stdout, p.stdout


@check("没有 check-balance.sh → 余额那段 SKIP，且不影响退出码")
def t_balance_skip():
    p = run(make_repo(check_balance=False))
    assert p.returncode == 0, p.stdout
    assert "SKIP" in p.stdout, p.stdout


@check("默认模式没被改坏：仍然只校验部署文件并打印那句老话")
def t_validate_mode_unchanged():
    p = run(make_repo(), args=())
    assert p.returncode == 0, f"{p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "Compose configuration is valid." in p.stdout, p.stdout
    assert "健康检查" not in p.stdout, "默认模式不该跑体检"


@check("--help 打印用法，未知参数退出码 2")
def t_cli_contract():
    p = run(make_repo(), args=("--help",))
    assert p.returncode == 0, p.stdout
    assert "用法" in p.stdout and "--health" in p.stdout, p.stdout
    p2 = run(make_repo(), args=("--bogus",))
    assert p2.returncode == 2, f"未知参数应退出 2，实际 {p2.returncode}"


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

#!/usr/bin/env sh
# 部署文件校验 + 线上体检。
#
#   ./scripts/validate.sh            部署文件校验（CI 用，不需要 docker 跑起来的服务）
#   ./scripts/validate.sh --health   线上体检：容器/站点/数据库/备份新鲜度/待审队列/余额
#
# --health 只读，不改任何东西；每项打印 PASS/FAIL/SKIP，有任何 FAIL 就退出码 1。
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

MODE="validate"
case "${1:-}" in
  "")        MODE="validate" ;;
  --health)  MODE="health" ;;
  -h|--help)
    echo "用法: $0 [--health]"
    echo "  不带参数   校验 compose / .env 占位符 / config/agents.json"
    echo "  --health   线上体检（容器、站点、数据库、备份新鲜度、待审队列、API 余额）"
    exit 0
    ;;
  *)
    echo "未知参数: $1（可用 --health 或 --help）" >&2
    exit 2
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker with Compose v2 is required." >&2
  exit 1
fi

# --------------------------------------------------------------------------
# 体检模式
# --------------------------------------------------------------------------
if [ "$MODE" = "health" ]; then
  FAIL=0
  SKIP=0

  pass() { printf '  PASS  %s\n' "$1"; }
  fail() { printf '  FAIL  %s\n' "$1"; FAIL=$((FAIL + 1)); }
  skip() { printf '  SKIP  %s\n' "$1"; SKIP=$((SKIP + 1)); }

  # 有 .env 就用真实域名，否则退回示例值（体检在服务器上跑才有意义）
  ENV_FILE=".env.example"
  if [ -f .env ]; then ENV_FILE=".env"; fi
  envval() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -n 1 | tr -d '"' | tr -d "'"; }

  CHAT_DOMAIN=$(envval CHAT_DOMAIN)
  CHAT_DOMAIN=${CHAT_DOMAIN:-chat.wfla-ailab.top}
  TUTORIAL_DOMAIN=$(envval TUTORIAL_DOMAIN)
  TUTORIAL_DOMAIN=${TUTORIAL_DOMAIN:-tutorial.wfla-ailab.top}
  BACKUP_MAX_AGE_HOURS=${BACKUP_MAX_AGE_HOURS:-48}

  echo "== 1/6 容器状态 =="
  SERVICES=$(docker compose --env-file "$ENV_FILE" config --services 2>/dev/null || true)
  RUNNING=$(docker compose --env-file "$ENV_FILE" ps --status running --services 2>/dev/null || true)
  if [ -z "$SERVICES" ]; then
    fail "读不到服务列表（docker compose config --services 失败）"
  else
    for svc in $SERVICES; do
      if printf '%s\n' "$RUNNING" | grep -qx "$svc"; then
        pass "容器 $svc 运行中"
      else
        fail "容器 $svc 没在运行（docker compose up -d 之后再看）"
      fi
    done
  fi

  echo "== 2/6 站点可达 =="
  check_url() {
    # $1 = 说明，$2 = URL
    code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$2" 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then
      pass "$1 → 200（$2）"
    else
      fail "$1 → $code（$2）"
    fi
  }
  check_url "聊天站首页" "https://$CHAT_DOMAIN/"
  check_url "擂台登录页" "https://$CHAT_DOMAIN/arena/login"
  check_url "门户页" "https://$CHAT_DOMAIN/portal"
  check_url "教程站首页" "https://$TUTORIAL_DOMAIN/"

  echo "== 3/6 数据库完整性 =="
  if docker compose --env-file "$ENV_FILE" exec -T postgres \
       pg_isready -U openwebui -d openwebui >/dev/null 2>&1; then
    pass "postgres 接受连接"
  else
    fail "postgres 连不上（pg_isready 失败）"
  fi
  PG_TABLES=$(docker compose --env-file "$ENV_FILE" exec -T postgres \
    psql -U openwebui -d openwebui -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null || true)
  case "$PG_TABLES" in
    '' | *[!0-9]* | 0)
      fail "postgres 查询失败或库是空的（拿到：${PG_TABLES:-空}）"
      ;;
    *)
      pass "postgres 能查表（public schema 有 $PG_TABLES 张表）"
      ;;
  esac
  ARENA_INTEGRITY=$(docker compose --env-file "$ENV_FILE" exec -T arena \
    python -c "import sqlite3;print(sqlite3.connect('/data/arena.db').execute('PRAGMA integrity_check').fetchone()[0])" \
    2>/dev/null | tr -d '\r' || true)
  if [ "$ARENA_INTEGRITY" = "ok" ]; then
    pass "擂台库 /data/arena.db integrity_check = ok"
  else
    fail "擂台库 integrity_check = ${ARENA_INTEGRITY:-读不到}（备份里应有可回滚的 arena-*.db）"
  fi

  echo "== 4/6 备份新鲜度（阈值 ${BACKUP_MAX_AGE_HOURS} 小时）=="
  LATEST_MANIFEST=$(ls -1t runtime/backups/MANIFEST-*.sha256 2>/dev/null | head -n 1 || true)
  if [ -z "$LATEST_MANIFEST" ]; then
    fail "runtime/backups/ 里没有 MANIFEST-*.sha256（backup.sh 从没成功跑过？）"
  elif [ -n "$(find "$LATEST_MANIFEST" -mmin -"$((BACKUP_MAX_AGE_HOURS * 60))" 2>/dev/null || true)" ]; then
    pass "最新备份在 ${BACKUP_MAX_AGE_HOURS} 小时内：$LATEST_MANIFEST"
  else
    fail "最新备份已超过 ${BACKUP_MAX_AGE_HOURS} 小时：$LATEST_MANIFEST"
  fi
  LATEST_ARENA_DB=$(ls -1t runtime/backups/arena-*.db 2>/dev/null | head -n 1 || true)
  if [ -n "$LATEST_ARENA_DB" ]; then
    pass "擂台库有独立备份：$LATEST_ARENA_DB"
  else
    fail "没有 arena-*.db（擂台数据没进备份 = 丢了就真没了）"
  fi

  echo "== 5/6 待审队列 =="
  QUEUE=$(docker compose --env-file "$ENV_FILE" exec -T arena python - <<'PY' 2>/dev/null | tr -d '\r' || true
import sqlite3
conn = sqlite3.connect("/data/arena.db")


def count(sql):
    try:
        return conn.execute(sql).fetchone()[0]
    except Exception:
        return "-"


print("待审Agent %s / 待审作品 %s / 开通申请 %s / 平台用户 %s" % (
    count("SELECT COUNT(*) FROM agents WHERE status='pending'"),
    count("SELECT COUNT(*) FROM projects WHERE status='pending'"),
    count("SELECT COUNT(*) FROM join_requests WHERE handled=0"),
    count("SELECT COUNT(*) FROM users"),
))
PY
)
  if [ -n "$QUEUE" ]; then
    pass "$QUEUE"
  else
    fail "读不到待审队列（arena 容器或 /data/arena.db 有问题）"
  fi

  echo "== 6/6 API 余额 =="
  if [ ! -x scripts/check-balance.sh ]; then
    skip "scripts/check-balance.sh 不存在或不可执行"
  else
    BALANCE_OUT=$(sh scripts/check-balance.sh 2>&1 || true)
    if printf '%s' "$BALANCE_OUT" | grep -q '余额'; then
      pass "$(printf '%s' "$BALANCE_OUT" | tr '\n' ' ')"
    else
      skip "余额没查到（Key 未配或网络不通）：$(printf '%s' "$BALANCE_OUT" | tr '\n' ' ' | cut -c1-120)"
    fi
  fi

  echo ""
  if [ "$FAIL" -eq 0 ]; then
    echo "健康检查通过（$SKIP 项跳过）"
    exit 0
  fi
  echo "健康检查失败：$FAIL 项 FAIL、$SKIP 项 SKIP"
  exit 1
fi

# --------------------------------------------------------------------------
# 部署文件校验（默认模式；CI 只跑这一段）
# --------------------------------------------------------------------------
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example \
  -f compose.yaml -f compose.tunnel.yaml config --quiet

if [ -f .env ] && grep -q '__[A-Z_]*__' .env; then
  echo "Unresolved placeholder found in .env." >&2
  exit 1
fi

if [ ! -f config/agents.json ]; then
  echo "config/agents.json is missing." >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  if ! python3 -m json.tool config/agents.json >/dev/null 2>&1; then
    echo "config/agents.json is not valid JSON." >&2
    exit 1
  fi
fi

echo "Compose configuration is valid."

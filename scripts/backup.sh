#!/usr/bin/env sh
# 全量备份：聊天站 postgres + 擂台 sqlite + 过滤器计数 + .env，并生成校验和清单。
#
# 用法（在服务器上）：
#     cd /opt/team-chat-relay && ./scripts/backup.sh
#
# 为什么擂台要单独备份：arena 用的是容器内 sqlite（卷 arena_data:/data/arena.db），
# 不在 postgres 里，pg_dump 抓不到它。以前只备 postgres，等于擂台数据完全没有兜底。
# 这里用 sqlite3 的在线热备（conn.backup()），即使 arena 正在写入也不会拿到撕裂的库，
# 并且备份完立刻做 integrity_check —— 备份"存在"不等于"可用"。
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

mkdir -p runtime/backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
PG_OUT="openwebui-$STAMP.sql"
ARENA_OUT="arena-$STAMP.db"
ENV_OUT="env-$STAMP.env"
COUNTER_OUT="filters-$STAMP.json"
ARENA_TMP="/tmp/arena-backup-$STAMP.db"

# ---- 1. 聊天站数据库（postgres）-----------------------------------------
docker compose exec -T postgres pg_dump -U openwebui -d openwebui > "runtime/backups/$PG_OUT"
chmod 600 "runtime/backups/$PG_OUT"
echo "OK  聊天站数据库  runtime/backups/$PG_OUT"

# ---- 2. 擂台数据库（sqlite 在线热备 + 立刻自检）-------------------------
docker compose exec -T -e ARENA_TMP="$ARENA_TMP" arena python - <<'PY'
import os
import sqlite3
import sys

src = os.environ.get("ARENA_DB", "/data/arena.db")
dst = os.environ["ARENA_TMP"]
if not os.path.exists(src):
    print("!! 找不到擂台数据库：" + src, file=sys.stderr)
    sys.exit(1)
if os.path.exists(dst):
    os.remove(dst)
con = sqlite3.connect(src)
bak = sqlite3.connect(dst)
with bak:
    con.backup(bak)          # 在线热备，等效于 sqlite3 的 .backup
bak.close()
con.close()
check = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
print("擂台数据库热备完成：%d 字节，integrity_check=%s" % (os.path.getsize(dst), check))
if check != "ok":
    print("!! 备份文件完整性检查未通过，中止备份", file=sys.stderr)
    sys.exit(1)
PY
docker compose cp "arena:$ARENA_TMP" "runtime/backups/$ARENA_OUT"
docker compose exec -T arena rm -f "$ARENA_TMP"
chmod 600 "runtime/backups/$ARENA_OUT"
echo "OK  擂台数据库    runtime/backups/$ARENA_OUT"

# ---- 3. 过滤器计数（丢了只是当日限额重置，不当作失败）-------------------
if docker compose cp open-webui:/app/backend/data/daily_message_usage.json \
     "runtime/backups/$COUNTER_OUT" 2>/dev/null; then
  chmod 600 "runtime/backups/$COUNTER_OUT"
  echo "OK  过滤器计数    runtime/backups/$COUNTER_OUT"
else
  echo "--  跳过过滤器计数（聊天站里没有该文件，属正常）"
fi

# ---- 4. 环境变量（含全部密钥；权限 600，别外发）-------------------------
if [ -f .env ]; then
  cp .env "runtime/backups/$ENV_OUT"
  chmod 600 "runtime/backups/$ENV_OUT"
  echo "OK  环境变量      runtime/backups/$ENV_OUT（含密钥，勿外发）"
fi

# ---- 5. 校验和清单 + 立即校验 -------------------------------------------
(
  cd runtime/backups
  sha256sum "$PG_OUT" "$ARENA_OUT" > "MANIFEST-$STAMP.sha256"
  if [ -f "$ENV_OUT" ]; then sha256sum "$ENV_OUT" >> "MANIFEST-$STAMP.sha256"; fi
  if [ -f "$COUNTER_OUT" ]; then sha256sum "$COUNTER_OUT" >> "MANIFEST-$STAMP.sha256"; fi
  sha256sum -c "MANIFEST-$STAMP.sha256"
)
echo "OK  校验和清单    runtime/backups/MANIFEST-$STAMP.sha256"

# ---- 6. 只保留最近 7 天 --------------------------------------------------
find runtime/backups -name 'openwebui-*.sql' -type f -mtime +7 -delete
find runtime/backups -name 'arena-*.db' -type f -mtime +7 -delete
find runtime/backups -name 'env-*.env' -type f -mtime +7 -delete
find runtime/backups -name 'filters-*.json' -type f -mtime +7 -delete
find runtime/backups -name 'MANIFEST-*.sha256' -type f -mtime +7 -delete
echo "旧备份（7 天前）已清理。"

cat <<'TIP'

恢复方法（需要时照着做）：
  聊天站：docker compose exec -T postgres psql -U openwebui -d openwebui \
            < runtime/backups/openwebui-<时间戳>.sql
  擂台：  docker compose stop arena
          docker compose cp runtime/backups/arena-<时间戳>.db arena:/data/arena.db
          docker compose start arena
  验真：  cd runtime/backups && sha256sum -c MANIFEST-<时间戳>.sha256

注意：runtime/backups/ 不要传到公共网盘 —— env-*.env 是全套密钥，*.sql / *.db 含成员数据。
      学期末建议手动拷一份到学校电脑或 U 盘，别让备份和服务器同时消失。
TIP

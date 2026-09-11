#!/usr/bin/env sh
# 白名单收紧 —— 让白名单对「聊天站自己的登录表单」也生效。
#
#   bash scripts/enforce-allowlist.sh                       # 预览（dry-run，什么都不改）
#   bash scripts/enforce-allowlist.sh --apply               # 真的降级
#   bash scripts/enforce-allowlist.sh --apply --include teacher@wfla-ailab.top
#
# 为什么需要它：/arena/school-login 那条路本来就查白名单，但聊天站原生登录
# （以及教程站 /login）走的是 Open WebUI 自己的 signin 接口，它不查白名单 ——
# 这就是交接文档 §6 记录的 P1-3 缺口。聊天站的登录接口不在我们代码里，没法
# 在中间拦截；能改的是账号的 role：role=pending 的账号登不进来。
# 所以这里做一次"清扫"：把不在白名单里的账号降级为 pending。
#
# 只改 role：不删用户、不改密码、不碰管理员（role=admin 与 WEBUI_ADMIN_EMAIL）。
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "需要 docker（这个脚本在服务器上跑）。" >&2
  exit 1
fi

ENV_FILE=".env.example"
if [ -f .env ]; then ENV_FILE=".env"; fi

exec docker compose --env-file "$ENV_FILE" exec -T arena \
  python /app/enforce_allowlist.py "$@"

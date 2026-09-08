#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

if [ ! -f .env ]; then
  echo "Missing local configuration. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

if grep -q '^CHAT_DOMAIN=chat\.example\.org$' .env; then
  echo "Set the real CHAT_DOMAIN in .env first." >&2
  exit 1
fi

if grep -q '^WEBUI_ADMIN_EMAIL=admin@example\.org$' .env; then
  echo "Set the real WEBUI_ADMIN_EMAIL in .env first." >&2
  exit 1
fi

# 防呆：管理员邮箱必须是合法格式（曾误填缺 .com 的地址导致管理员登录失败）
if ! grep -E '^WEBUI_ADMIN_EMAIL=[^@]+@[^@]+\.[^@]+$' .env >/dev/null; then
  echo "WEBUI_ADMIN_EMAIL does not look like a valid email (missing domain?). Fix .env." >&2
  exit 1
fi

if grep -q '^DEEPSEEK_API_KEY=__DEEPSEEK_API_KEY__$' .env; then
  echo "Paste your DeepSeek API key into DEEPSEEK_API_KEY in .env first." >&2
  exit 1
fi

./scripts/validate.sh
docker compose pull
docker compose up -d --remove-orphans
docker compose ps

#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

if [ ! -f .env ]; then
  echo "Missing local configuration. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

if grep -q '^WEBUI_ADMIN_EMAIL=admin@example\.org$' .env; then
  echo "Set the real WEBUI_ADMIN_EMAIL in .env first." >&2
  exit 1
fi

if grep -q '^DEEPSEEK_API_KEY=__DEEPSEEK_API_KEY__$' .env; then
  echo "Paste your DeepSeek API key into DEEPSEEK_API_KEY in .env first." >&2
  exit 1
fi

./scripts/validate.sh
docker compose -f compose.yaml -f compose.tunnel.yaml pull
docker compose -f compose.yaml -f compose.tunnel.yaml up -d --remove-orphans
docker compose -f compose.yaml -f compose.tunnel.yaml ps

echo "Open an SSH tunnel from your computer:"
echo "  ssh -L 3000:127.0.0.1:3000 <user>@<server>"
echo "Then browse to http://127.0.0.1:3000"

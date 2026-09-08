#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker with Compose v2 is required." >&2
  exit 1
fi

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

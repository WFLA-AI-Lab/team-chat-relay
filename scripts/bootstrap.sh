#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to generate local secrets." >&2
  exit 1
fi

if [ -e .env ]; then
  echo "Refusing to overwrite an existing .env." >&2
  exit 1
fi

umask 077
cp .env.example .env

POSTGRES_PASSWORD=$(openssl rand -hex 24)
WEBUI_SECRET_KEY=$(openssl rand -hex 32)
ARENA_SECRET=$(openssl rand -hex 24)
WEBUI_ADMIN_PASSWORD=$(openssl rand -hex 18)

sed -i "s/__POSTGRES_PASSWORD__/$POSTGRES_PASSWORD/g" .env
sed -i "s/__WEBUI_SECRET_KEY__/$WEBUI_SECRET_KEY/g" .env
sed -i "s/__ARENA_SECRET__/$ARENA_SECRET/g" .env
sed -i "s/__WEBUI_ADMIN_PASSWORD__/$WEBUI_ADMIN_PASSWORD/g" .env

mkdir -p runtime/backups
chmod 600 .env
chmod 700 runtime runtime/backups

echo "Local secrets created."
echo "Next: edit CHAT_DOMAIN and WEBUI_ADMIN_EMAIL in .env."
echo "Then paste your DeepSeek API key into DEEPSEEK_API_KEY in .env"
echo "  (create one at https://platform.deepseek.com -> API Keys)."
echo "Admin password is stored only in the local .env file."

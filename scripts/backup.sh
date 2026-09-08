#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

mkdir -p runtime/backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUTPUT="runtime/backups/openwebui-$STAMP.sql"

docker compose exec -T postgres pg_dump -U openwebui -d openwebui > "$OUTPUT"
chmod 600 "$OUTPUT"
echo "Database backup written to $OUTPUT"
echo "It includes member accounts, chats, and agent definitions."
echo "The DeepSeek API key lives only in .env; protect that file separately."

# Keep only the latest 7 backups.
find runtime/backups -name 'openwebui-*.sql' -type f -mtime +7 -delete
echo "Old backups older than 7 days removed (retention: 7)."

#!/usr/bin/env sh
# Install daily cron jobs on the server:
#   - 03:15 every day: database backup (keeps the latest 7 backups)
#   - 08:30 every day: check DeepSeek balance and warn when low
# Run once as the user that owns the project (relayadmin).
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

if ! command -v crontab >/dev/null 2>&1; then
  echo "cron is not installed. Try: sudo apt install -y cron && sudo systemctl enable --now cron" >&2
  exit 1
fi

mkdir -p runtime/backups

# Remove any previous entries we installed, then append fresh ones.
( crontab -l 2>/dev/null | grep -v 'team-chat-relay' || true
  echo "15 3 * * * cd $REPO_DIR && ./scripts/backup.sh >> runtime/backups/cron.log 2>&1"
  echo "30 8 * * * cd $REPO_DIR && ./scripts/check-balance.sh >> runtime/backups/balance.log 2>&1"
) | crontab -

echo "Cron installed for $REPO_DIR:"
crontab -l | grep team-chat-relay
echo "Backup runs daily 03:15; balance check daily 08:30 (server timezone)."

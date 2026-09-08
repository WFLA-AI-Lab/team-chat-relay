#!/usr/bin/env sh
# Check the DeepSeek account balance and warn when it is low.
# Run manually or via cron (see scripts/setup-cron.sh).
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_DIR"

if [ ! -f .env ]; then
  echo "Missing .env. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

KEY=$(sed -n 's/^DEEPSEEK_API_KEY=//p' .env | tail -n 1 | tr -d '"' | tr -d "'")
if [ -z "$KEY" ] || [ "$KEY" = "__DEEPSEEK_API_KEY__" ]; then
  echo "DEEPSEEK_API_KEY is not set in .env." >&2
  exit 1
fi

BASE_URL=$(sed -n 's/^DEEPSEEK_BASE_URL=//p' .env | tail -n 1 | tr -d '"' | tr -d "'")
BASE_URL=${BASE_URL:-https://api.deepseek.com}

JSON=$(curl -fsS --noproxy '*' -H "Authorization: Bearer $KEY" "$BASE_URL/user/balance")

echo "$JSON" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("无法解析余额响应"); sys.exit(0)
infos = data.get("balance_infos", [])
for info in infos:
    cur = info.get("currency", "?")
    total = info.get("total_balance", "?")
    print(f"余额({cur}): {total}")
    try:
        if float(total) < 10:
            print("⚠️ 余额不足 10 元，请尽快充值！https://platform.deepseek.com")
    except ValueError:
        pass
if not infos:
    print("响应中没有余额信息:", json.dumps(data, ensure_ascii=False))
' || echo "$JSON"

#!/bin/sh
echo "=== chats.py 列表处理器(215-262行) ==="
sed -n '215,262p' /app/backend/open_webui/routers/chats.py
echo "=== get_chats_by_user_id 定义 ==="
grep -n "def get_chats_by_user_id" -A 30 /app/backend/open_webui/models/chats.py | head -45

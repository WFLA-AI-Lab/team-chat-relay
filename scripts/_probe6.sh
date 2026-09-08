#!/bin/sh
echo "=== get_chat_title_id_list_by_user_id 定义 ==="
grep -n "def get_chat_title_id_list_by_user_id" -A 45 /app/backend/open_webui/models/chats.py | head -55
echo "=== folders 相关查询 ==="
grep -n "folder_id\|include_folders" /app/backend/open_webui/models/chats.py | head -20

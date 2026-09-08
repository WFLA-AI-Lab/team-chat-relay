"""
主站每日消息限额过滤器
======================
给聊天站（Open WebUI）加「每人每天最多 N 条消息」的限制，防止有人刷爆
DeepSeek 预算。默认每天 300 条（额度比较宽松），可以在
Admin Panel → Functions → 主站每日消息限额 → Valves 里改 daily_limit。

计数存在 /app/backend/data/daily_message_usage.json（open_webui_data 卷），
重启不丢。仅统计用户主动发送的消息；AI 的回复不计数。
"""

import datetime
import json
import os

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        daily_limit: int = Field(
            default=300, description="每个用户每天最大消息数（主站聊天）"
        )
        data_path: str = Field(
            default="/app/backend/data/daily_message_usage.json",
            description="计数文件路径（open_webui_data 卷内，重启不丢）",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _load(self):
        try:
            with open(self.valves.data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data):
        try:
            os.makedirs(os.path.dirname(self.valves.data_path), exist_ok=True)
            with open(self.valves.data_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def inlet(self, body):
        user = body.get("user") or {}
        email = user.get("email") or user.get("id") or "unknown"
        today = datetime.date.today().isoformat()
        key = f"{email}:{today}"
        data = self._load()
        count = data.get(key, 0) + 1
        if count > self.valves.daily_limit:
            raise Exception(
                f"今日消息数已达上限（{self.valves.daily_limit} 条），请明天再试。"
                "如有特殊情况请联系管理员。"
            )
        data[key] = count
        self._save(data)
        return body

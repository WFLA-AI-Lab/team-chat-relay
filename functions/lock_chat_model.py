"""
预设对话模型锁定过滤器
========================
管理员预置的 Agent 对话（chat_id -> 固定模型）的模型被锁定：
成员在这些对话里切换模型会被拦截并提示，保证每个 Agent 对话始终使用
它绑定的模型；成员自己新建的对话不受影响。

锁定表文件：/app/backend/data/locked_chat_models.json（open_webui_data 卷，
重启不丢），由 scripts/seed-starter-chats.py 在预置对话时生成。
"""

import json

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        data_path: str = Field(
            default="/app/backend/data/locked_chat_models.json",
            description="锁定表路径（chat_id -> {model, name}）",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _load(self):
        try:
            with open(self.valves.data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def inlet(self, body):
        # v0.11 里 chat_id 位于 body["metadata"] 中
        metadata = body.get("metadata") or {}
        chat_id = metadata.get("chat_id") or body.get("chat_id")
        if not chat_id:
            return body
        locks = self._load()
        entry = locks.get(chat_id)
        if not entry:
            return body
        model = body.get("model")
        if model and model != entry.get("model"):
            raise Exception(
                f"该对话的模型已锁定为「{entry.get('name', '')}」，不能更换模型。"
                "如需其他模型，请新建对话。"
            )
        return body

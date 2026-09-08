"""本地干跑 scripts/seed-starter-chats.py（模拟 psql/subprocess，不上服务器、不联网）。

- exec 真实脚本（__name__="seed"，避免触发 main 的 sys.exit 自启动）；
- 断言动态化：chat INSERT 数 == config/agents.json 里的 Agent 数（不硬编码，4 个或 7 个都能过）；
- 保留原结构断言：folder INSERT=0、chat INSERT 不含 folder_id、含 archived/pinned(false)、
  history 含 2 条消息、assistant done=True；
- welcome 非空的 Agent：欢迎语 == 该字段值；没有 welcome 的 Agent：欢迎语含 Agent 名；
- 必须在纯 Windows python 下跑通，不依赖 docker、不联网。
"""
import json
import os
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
seed_path = os.path.join(ROOT, "scripts", "seed-starter-chats.py")

with open(os.path.join(ROOT, "config", "agents.json"), encoding="utf-8") as fh:
    agents_cfg = json.load(fh).get("agents", [])

ns = {"__file__": seed_path, "__name__": "seed"}
with open(seed_path, "r", encoding="utf-8") as fh:
    exec(compile(fh.read(), seed_path, "exec"), ns)

calls = []

# 贴近真实 Open WebUI v0.11 chat/folder 表结构
FOLDER_SCHEMA = [
    ["id", "NO", None, "uuid"], ["user_id", "NO", None, "uuid"],
    ["name", "NO", None, "text"], ["is_expanded", "NO", None, "boolean"],
    ["created_at", "NO", None, "bigint"], ["updated_at", "NO", None, "bigint"],
    ["color", "YES", None, "text"],
]
CHAT_SCHEMA = [
    ["id", "NO", None, "uuid"], ["user_id", "NO", None, "uuid"],
    ["chat", "NO", None, "jsonb"], ["folder_id", "YES", None, "uuid"],
    ["title", "YES", None, "text"],
    ["archived", "NO", None, "boolean"], ["pinned", "NO", None, "boolean"],
    ["created_at", "NO", None, "bigint"], ["updated_at", "NO", None, "bigint"],
]


def fake_psql(sql, rows=False):
    calls.append(sql)
    if "information_schema.columns" in sql:
        if "'chat'" in sql:
            return CHAT_SCHEMA
        if "'folder'" in sql:
            return FOLDER_SCHEMA
        return []
    if 'FROM "user"' in sql:
        return [["u-1", "member@test.com", "user"]]
    if "FROM chat" in sql:
        return []
    return None


ns["psql"] = fake_psql

# 桩掉 docker 调用（锁定表写入），避免真正调用 docker / 联网
fake_sub = types.SimpleNamespace(
    run=lambda *a, **k: types.SimpleNamespace(returncode=0, stderr="")
)
ns["subprocess"] = fake_sub

rc = ns["main"]()
print("=== 退出码:", rc, "===")

inserts = [c for c in calls if c.strip().startswith("INSERT INTO chat")]
finserts = [c for c in calls if c.strip().startswith("INSERT INTO folder")]
print("folder INSERT 数:", len(finserts), "(应为0，不再建文件夹) | chat INSERT 数:", len(inserts),
      "(应为 len(agents) =", len(agents_cfg), ")")

assert rc == 0, "main() 应成功退出"
assert len(finserts) == 0, "不应再创建文件夹（v0.11 会隐藏带 folder_id 的对话）"
assert len(inserts) == len(agents_cfg), (
    f"chat INSERT 数应为 {len(agents_cfg)}（每个 Agent 一条），实际 {len(inserts)}")

c_sql = inserts[0]
col_part = c_sql.split("(", 1)[1].split(")", 1)[0]
assert "folder_id" not in col_part, "chat INSERT 不应含 folder_id 列"
assert "archived" in c_sql and "pinned" in c_sql, "chat INSERT 应含 archived/pinned"
assert "false" in c_sql, "archived/pinned 应为 false"
print("chat INSERT:", c_sql[:240])

# 逐个 Agent 校验起始对话结构与欢迎语
for a in agents_cfg:
    name = a["name"]
    model = a.get("model", "deepseek-chat")
    welcome_field = a.get("welcome", "")
    chat = ns["build_starter_chat"](name, model, a.get("description", ""), welcome_field)
    assert chat["title"] == name
    assert chat["models"] == [model]
    hist = chat["history"]["messages"]
    assert len(hist) == 2, "起始对话应包含 1 条用户问候 + 1 条助手欢迎语"
    assistant = [m for m in hist.values() if m["role"] == "assistant"][0]
    assert assistant["done"] is True
    if welcome_field:
        assert assistant["content"] == welcome_field, (
            f"定义了 welcome 的 Agent「{name}」欢迎语应等于该字段值")
    else:
        assert name in assistant["content"], "未定义 welcome 的 Agent 欢迎语应含 Agent 名称"
    assert chat["messages"] == [{"role": "assistant", "content": assistant["content"]}]
    assert isinstance(chat["timestamp"], int) and chat["timestamp"] > 0

# 内存里模拟 welcome 逻辑（不改 agents.json）：
chat_w = ns["build_starter_chat"]("测试Agent", "deepseek-chat", "这是描述", "自定义欢迎语")
aw = [m for m in chat_w["history"]["messages"].values() if m["role"] == "assistant"][0]
assert aw["content"] == "自定义欢迎语", "有 welcome 时欢迎语应直接采用该值"
chat_d = ns["build_starter_chat"]("测试Agent", "deepseek-chat", "这是描述", "")
ad = [m for m in chat_d["history"]["messages"].values() if m["role"] == "assistant"][0]
assert "测试Agent" in ad["content"] and "这是描述" in ad["content"], "无 welcome 有 description 应走默认式"
chat_n = ns["build_starter_chat"]("测试Agent", "deepseek-chat", "", "")
an = [m for m in chat_n["history"]["messages"].values() if m["role"] == "assistant"][0]
assert "测试Agent" in an["content"], "无 welcome 无 description 应走默认式"

print("PASS 本地干跑通过：chat INSERT 数 == agents 数，欢迎语与结构正确")

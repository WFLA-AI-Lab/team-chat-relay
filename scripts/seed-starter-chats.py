#!/usr/bin/env python3
"""为每个成员预置「每个 Agent 一条对话」到左侧栏。

用法（服务器上，项目目录内）：

    python3 ./scripts/seed-starter-chats.py

用管理员身份直连 PostgreSQL（chat 表），为所有非管理员用户创建
config/agents.json 中每个 Agent 的起始对话：
- 标题 = Agent 名，已绑定对应模型（点开即聊，不用碰模型选择器）
- 对话为「空对话」形态，与 Open WebUI 新建对话的结构一致
- 已存在同标题对话的用户会跳过，可重复运行（新成员加入后重跑即可）

前提：先建好成员账号（主站 Admin Panel -> Users -> Add User）。
"""

import json
import os
import subprocess
import sys
import time
import uuid

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def psql(sql, rows=False):
    """在 postgres 容器里执行 SQL，返回行列表（rows=True）或 None。"""
    cmd = [
        "docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", "openwebui", "-d", "openwebui",
        "-t", "-A",
    ]
    if rows:
        cmd += ["-F", "|", "-c", sql]
    else:
        cmd += ["-c", sql]
    proc = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"psql 失败: {proc.stderr.strip()[:400]}")
    if not rows:
        return None
    out = proc.stdout.strip()
    if not out:
        return []
    return [line.split("|") for line in out.splitlines()]


def sql_str(value):
    """安全地把值嵌入 SQL 字符串字面量（单引号翻倍）。"""
    return "'" + str(value).replace("'", "''") + "'"


def sql_val(value):
    """把 Python 值格式化成 SQL 字面量（布尔不加引号）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return sql_str(value)


def table_meta(name):
    """返回 {column: {nullable, default, type}}，自动发现表结构。"""
    rows = psql(
        "SELECT column_name, is_nullable, column_default, data_type "
        "FROM information_schema.columns WHERE table_name = " + sql_str(name),
        rows=True,
    )
    meta = {}
    for r in rows:
        if len(r) >= 4:
            meta[r[0]] = {
                "nullable": r[1].strip().upper() == "YES",
                "default": r[2].strip() if r[2] else None,
                "type": (r[3] or "").lower(),
            }
    return meta


def default_for(col, m, now_ms):
    """为「非空且无默认值」的列生成合理默认值。"""
    t = m["type"]
    if "boolean" in t:
        return False
    if "bigint" in t or "integer" in t or "numeric" in t or "double" in t:
        return 0
    if "timestamp" in t or "date" in t:
        return now_ms
    return ""


def build_insert(table, meta, known, now_ms):
    """按表结构生成 INSERT：已知值优先，其余非空无默认列自动补默认。"""
    cols, vals = [], []
    for col, m in meta.items():
        if col in known:
            cols.append(col)
            vals.append(sql_val(known[col]))
        elif not m["nullable"] and not m["default"]:
            cols.append(col)
            vals.append(sql_val(default_for(col, m, now_ms)))
    return "INSERT INTO " + table + " (" + ", ".join(cols) + ") VALUES (" + \
        ", ".join(vals) + ");"


def build_starter_chat(title, model, description="", welcome=""):
    """生成带欢迎语的起始对话（user「你好」→ assistant 自我介绍）。

    welcome 非空时直接用作欢迎语（agents.json 里的「welcome」字段）；
    否则回退为默认欢迎语（含名称与介绍）。
    """
    chat_id = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    if not welcome and description:
        welcome = f"你好！我是「{title}」。{description}有什么可以帮你？"
    elif not welcome:
        welcome = f"你好！我是「{title}」。有什么可以帮你？"
    u_id = str(uuid.uuid4())
    a_id = str(uuid.uuid4())
    o_id = "msg_" + uuid.uuid4().hex
    messages = {
        u_id: {
            "id": u_id, "parentId": None, "childrenIds": [a_id],
            "role": "user", "content": "你好", "timestamp": now_ms, "models": [model],
        },
        a_id: {
            "id": a_id, "parentId": u_id, "childrenIds": [], "role": "assistant",
            "content": welcome, "done": True, "model": model,
            "timestamp": now_ms + 1, "modelIdx": 0,
            "output": [{
                "type": "message", "id": o_id, "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": welcome}],
            }],
        },
    }
    return {
        "id": chat_id,
        "title": title,
        "models": [model],
        "history": {"currentId": a_id, "messages": messages},
        "messages": [{"role": "assistant", "content": welcome}],
        "files": [],
        "tags": [],
        "timestamp": now_ms,
    }


def main():
    with open(os.path.join(REPO_DIR, "config", "agents.json"), "r", encoding="utf-8") as fh:
        agents = json.load(fh).get("agents", [])
    if not agents:
        print("config/agents.json 里没有 Agent。", file=sys.stderr)
        return 1

    # 1. 自动发现表结构
    chat_meta = table_meta("chat")
    for need in ("id", "user_id", "chat"):
        if need not in chat_meta:
            print(f"chat 表缺少列 {need}，当前列: {sorted(chat_meta)}", file=sys.stderr)
            return 1

    # 2. 取所有非管理员用户
    users = psql(
        "SELECT id, email, role FROM \"user\" ORDER BY created_at ASC", rows=True)
    targets = [u for u in users if len(u) >= 3 and (u[2] != "admin" or os.environ.get("ARENA_SEED_ALL") == "1")]
    if not targets:
        print("没有找到成员账号。请先在主站 Admin Panel -> Users -> Add User 建成员。",
              file=sys.stderr)
        return 1
    print(f"将为 {len(targets)} 个成员账号预置起始对话：{', '.join(t[1] for t in targets)}")

    # 3. 逐用户、逐 Agent 建对话（直接出现在侧边栏普通列表）
    #    注意：v0.11 默认对话列表会排除带 folder_id 的对话（include_folders=false），
    #    所以这里不建文件夹、不设 folder_id。
    now_ms = int(time.time() * 1000)  # chat 表的 created_at/updated_at 是 bigint 毫秒
    created_total = skipped_total = 0
    for user_id, email, _role in targets:
        existing_titles = {
            r[0]
            for r in psql(
                "SELECT (chat::jsonb->>'title') FROM chat WHERE user_id = " + sql_str(user_id),
                rows=True,
            )
        }
        for agent in agents:
            title = agent.get("name") or agent.get("id")
            model = agent.get("model", "deepseek-chat")
            if title in existing_titles:
                skipped_total += 1
                continue
            chat_json = build_starter_chat(title, model, agent.get("description", ""),
                                           agent.get("welcome", ""))
            values = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                # title 列必须写（侧边栏列表读的是 title 列，不是 JSON 里的）
                "title": title,
                "chat": json.dumps(chat_json, ensure_ascii=False),
                # archived/pinned 必须显式 false，否则 NULL 会让侧边栏不显示
                "archived": False,
                "pinned": False,
                "created_at": now_ms,
                "updated_at": now_ms,
            }
            psql(build_insert("chat", chat_meta, values, now_ms))
            created_total += 1
            print(f"  + {email}: {title}")

    # 4. 生成「模型锁定表」写入 Open WebUI 数据卷（lock_chat_model 过滤器使用）
    write_lock_map(agents, targets, now_ms)

    print(f"完成：新建 {created_total} 条，跳过已存在 {skipped_total} 条。")
    print("刷新主站即可在左侧栏看到。新成员加入后重跑本脚本。")
    return 0


def write_lock_map(agents, targets, now_ms):
    """把预置对话的 chat_id -> {model, name} 写入容器内锁定表。"""
    titles_sql = ", ".join(sql_str(a.get("name") or a.get("id")) for a in agents)
    lock = {}
    for user_id, _email, _role in targets:
        rows = psql(
            "SELECT id, chat::jsonb->>'models', chat::jsonb->>'title' FROM chat "
            "WHERE user_id = " + sql_str(user_id)
            + " AND (chat::jsonb->>'title') IN (" + titles_sql + ")",
            rows=True,
        )
        for rid, models_json, title in rows:
            try:
                model = (json.loads(models_json or "[]") or [None])[0]
            except Exception:
                model = None
            if model:
                lock[rid] = {"model": model, "name": title}
    if not lock:
        print("（没有可锁定的预置对话）")
        return
    payload = json.dumps(lock, ensure_ascii=False)
    cmd = [
        "docker", "compose", "exec", "-T", "open-webui", "sh", "-c",
        "cat > /app/backend/data/locked_chat_models.json",
    ]
    proc = subprocess.run(cmd, cwd=REPO_DIR, input=payload,
                          capture_output=True, text=True, timeout=60)
    if proc.returncode == 0:
        print(f"模型锁定表已更新：{len(lock)} 条对话固定模型")
    else:
        print(f"⚠️ 模型锁定表写入失败：{proc.stderr.strip()[:200]}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

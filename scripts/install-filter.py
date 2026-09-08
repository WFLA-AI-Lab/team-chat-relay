#!/usr/bin/env python3
"""上传 functions/ 目录下所有过滤器到 Open WebUI，并开启全局生效。

用法（在服务器上，项目目录内）：

    python3 ./scripts/install-filter.py

读取 .env 的管理员凭据，把 functions/*.py 上传为 Open WebUI 的过滤器函数，
并通过 toggle/global 接口设为全局过滤器。已存在时自动更新。
"""

import json
import os
import sys
import urllib.error
import urllib.request

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.environ.get("OPEN_WEBUI_API", "http://127.0.0.1:8080")

# 函数 ID 只允许字母/数字/下划线（v0.11 源码 isidentifier() 校验）。
FILTERS = [
    {
        "id": "daily_message_limit",
        "name": "主站每日消息限额",
        "source": os.path.join(REPO_DIR, "functions", "daily_message_limit.py"),
    },
    {
        "id": "lock_chat_model",
        "name": "预设对话模型锁定",
        "source": os.path.join(REPO_DIR, "functions", "lock_chat_model.py"),
    },
]

urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)


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


def request(method, path, token=None, payload=None, exit_on_error=True):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if not exit_on_error:
            try:
                return exc.code, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return exc.code, raw
        print(f"HTTP {exc.code} {method} {path}: {raw}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        if not exit_on_error:
            return 0, str(exc)
        print(f"{method} {path} failed: {exc}", file=sys.stderr)
        sys.exit(1)


def install_one(token, fid, name, source):
    with open(source, "r", encoding="utf-8") as fh:
        code = fh.read()
    payload = {
        "id": fid,
        "name": name,
        "meta": {"description": name + "（functions/ 同步，可重复安装更新）"},
        "content": code,
    }
    status, body = request(
        "POST", "/api/v1/functions/create", token=token, payload=payload,
        exit_on_error=False,
    )
    if status in (200, 201):
        print(f"Function created: {fid}")
    else:
        status, body = request(
            "POST", f"/api/v1/functions/id/{fid}/update",
            token=token, payload=payload, exit_on_error=False,
        )
        if status in (200, 201):
            print(f"Function updated: {fid}")
        else:
            detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            print(f"Upload failed for {fid} (create and update both failed): {detail[:200]}",
                  file=sys.stderr)
            return False

    # 设为全局过滤器
    _, body = request("GET", f"/api/v1/functions/id/{fid}", token=token, exit_on_error=False)
    if not bool((body or {}).get("is_global")):
        status, body = request(
            "POST", f"/api/v1/functions/id/{fid}/toggle/global", token=token,
            exit_on_error=False,
        )
        if status in (200, 201):
            print(f"  global: {'ON' if (body or {}).get('is_global') else 'toggle called'}")
        else:
            print(f"  WARNING: 全局开关设置失败 ({status})，请稍后在 Functions 面板手动开启。",
                  file=sys.stderr)
    else:
        print("  global: already ON")

    # 激活过滤器（is_active 必须为 true 才会运行）
    if not bool((body or {}).get("is_active")):
        status, body = request(
            "POST", f"/api/v1/functions/id/{fid}/toggle", token=token, exit_on_error=False,
        )
        if status in (200, 201):
            print(f"  active: {'ON' if (body or {}).get('is_active') else 'toggle called'}")
        else:
            print(f"  WARNING: 激活失败 ({status})，请稍后在 Functions 面板手动开启。",
                  file=sys.stderr)
    else:
        print("  active: already ON")
    return True


def main():
    env = load_dotenv(os.path.join(REPO_DIR, ".env"))
    email = env.get("WEBUI_ADMIN_EMAIL", "")
    password = env.get("WEBUI_ADMIN_PASSWORD", "")
    if not email or not password or "__" in email + password:
        print("WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD missing in .env.", file=sys.stderr)
        return 1

    _, body = request(
        "POST", "/api/v1/auths/signin", payload={"email": email, "password": password}
    )
    token = (body or {}).get("token")
    if not token:
        print("Admin sign-in failed.", file=sys.stderr)
        return 1

    ok = True
    for f in FILTERS:
        ok = install_one(token, f["id"], f["name"], f["source"]) and ok
    print("Done.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

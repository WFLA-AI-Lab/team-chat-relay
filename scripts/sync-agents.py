#!/usr/bin/env python3
"""Sync agent definitions from config/agents.json into Open WebUI.

Run on the server (or anywhere that can reach the Open WebUI API):

    python3 ./scripts/sync-agents.py

It reads .env for WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD, signs in, and
creates or updates every agent in config/agents.json. Agents are then visible
to every member in the model picker (agent library).

Strategy: try UPDATE first for each agent id; if the model does not exist,
fall back to CREATE. This avoids depending on the (pagination/shape-sensitive)
model list endpoint.

Requires: python3 (stdlib only). Override the API base with OPEN_WEBUI_API
if needed (default http://127.0.0.1:8080, loopback only).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.environ.get("OPEN_WEBUI_API", "http://127.0.0.1:8080")

# Talk to the loopback API directly; ignore any http_proxy env vars that may
# be set on the server (they would otherwise hijack 127.0.0.1 requests).
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


class _Retryable(Exception):
    """Internal signal for transient failures (connection, empty body, bad JSON)."""


def request(method, path, token=None, payload=None, retries=6, delay=5,
            exit_on_error=True):
    """Call the Open WebUI API.

    - Transient failures (connection refused, empty body, non-JSON) are retried
      while the service is still starting up.
    - HTTP 4xx/5xx: if exit_on_error is True, print and exit; otherwise return
      (status, body) so the caller can branch (e.g. update-vs-create).
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
            if not body:
                raise _Retryable("empty response body")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError as exc:
                raise _Retryable(f"non-JSON body: {body[:120]!r}") from exc
        except _Retryable as exc:
            if attempt == retries:
                print(f"Failed after {retries} attempts ({method} {path}): {exc}",
                      file=sys.stderr)
                sys.exit(1)
            print(f"Retry {attempt}/{retries} ({method} {path}): {exc}", file=sys.stderr)
            time.sleep(delay)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if not exit_on_error:
                try:
                    return exc.code, json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    return exc.code, raw
            print(f"HTTP {exc.code} {method} {path}: {raw}", file=sys.stderr)
            sys.exit(1)
        except (urllib.error.URLError, OSError) as exc:
            if attempt == retries:
                print(f"Failed after {retries} attempts: {exc}", file=sys.stderr)
                sys.exit(1)
            print(f"Retry {attempt}/{retries} ({method} {path}): {exc}", file=sys.stderr)
            time.sleep(delay)


def main():
    env = load_dotenv(os.path.join(REPO_DIR, ".env"))
    email = env.get("WEBUI_ADMIN_EMAIL", "")
    password = env.get("WEBUI_ADMIN_PASSWORD", "")
    if not email or not password or "__" in email + password:
        print(
            "WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD missing in .env.",
            file=sys.stderr,
        )
        return 1

    with open(os.path.join(REPO_DIR, "config", "agents.json"), "r", encoding="utf-8") as fh:
        agents = json.load(fh).get("agents", [])
    if not agents:
        print("No agents found in config/agents.json.", file=sys.stderr)
        return 1

    _, body = request(
        "POST", "/api/v1/auths/signin", payload={"email": email, "password": password}
    )
    token = (body or {}).get("token")
    if not token:
        print("Admin sign-in failed; check credentials in .env.", file=sys.stderr)
        return 1

    created = updated = failed = 0
    for agent in agents:
        aid = agent.get("id")
        if not aid:
            print("Skipping an agent without 'id'.", file=sys.stderr)
            continue
        payload = {
            "id": aid,
            "name": agent.get("name", aid),
            # v0.11 的 update 接口重校验要求 access_grants 为列表（None 会 500）
            "access_grants": [],
            # 显式带上 base_model_id / profile_image_url，避免 None 值问题
            "base_model_id": agent.get("model", "deepseek-chat"),
            "meta": {
                "description": agent.get("description", ""),
                "capabilities": {"vision": False, "usage": True},
                "tags": [agent["tag"]] if agent.get("tag") else [],
                "profile_image_url": agent.get("profile_image_url", ""),
            },
            "params": {
                "system": agent.get("system", ""),
                "model": agent.get("model", "deepseek-chat"),
            },
        }

        status, body = request(
            "POST", "/api/v1/models/model/update", token=token, payload=payload,
            exit_on_error=False,
        )
        if status == 200:
            print(f"updated  {aid}")
            updated += 1
            continue

        # 模型不存在 → 走创建
        status2, body2 = request(
            "POST", "/api/v1/models/create", token=token, payload=payload,
            exit_on_error=False,
        )
        if status2 in (200, 201):
            print(f"created  {aid}")
            created += 1
        else:
            failed += 1
            detail = body2 if isinstance(body2, str) else json.dumps(body2, ensure_ascii=False)
            print(f"FAILED   {aid}: update={status} create={status2} {detail[:200]}",
                  file=sys.stderr)

    print(f"Done: {created} created, {updated} updated, {failed} failed, "
          f"{len(agents)} total.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

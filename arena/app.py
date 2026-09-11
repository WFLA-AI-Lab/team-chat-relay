#!/usr/bin/env python3
"""AI 社团 Agent 擂台 —— 成员提交 Agent、投票、排行榜、试聊。

独立的小型 Flask 应用：
- 登录复用 Open WebUI 账号（邮箱+密码调 Open WebUI API 校验）。
- 成员通过详细表单创建 Agent；提交时自动用「示例问题」试跑一遍，把结果反馈给作者；
  管理员在网页上通过/拒绝。
- 擂台展示所有 Agent（含未审核），按「学习/社团/其它」分类，支持收藏、留言、
  预览、直接试聊；试聊有限流（每分钟条数 + 每日总条数），防滥用。
- 每周每人 3 颗星（不能投自己、可撤销）；有「本周擂主」和用户周榜/总榜。
- 每周主题轮换（12 个主题循环），管理员可在审核页覆盖。
- GitHub 作品廊：成员提交仓库作品，管理员审核后经公开 API 供社团网站展示。

依赖：flask。环境变量见 compose.yaml 的 arena 服务。
"""

import datetime
import json
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.request

from flask import Flask, g, jsonify, make_response, redirect, render_template, request, session, url_for

import school_auth

# --------------------------------------------------------------------------
# 配置（来自环境变量）
# --------------------------------------------------------------------------
OPENWEBUI_URL = os.environ.get("OPENWEBUI_URL", "http://open-webui:8080").rstrip("/")
ADMIN_EMAIL = os.environ.get("WEBUI_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("WEBUI_ADMIN_PASSWORD", "")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
# 可选备用供应商：Key 留空即禁用该供应商（模型在前端下拉里也不会出现）。
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
JIYUAN_KEY = os.environ.get("JIYUAN_API_KEY", "")
JIYUAN_URL = os.environ.get("JIYUAN_BASE_URL", "https://tokenrhythm.studio/v1").rstrip("/")
SECRET = os.environ.get("ARENA_SECRET", "dev-secret-change-me")
DB_PATH = os.environ.get("ARENA_DB", "/data/arena.db")
# 擂台挂在聊天站同域名下的子路径（如 /arena），从而能复用聊天站的登录 cookie。
BASE_PATH = os.environ.get("ARENA_BASE_PATH", "/arena").rstrip("/")
CHAT_RATE_PER_MIN = int(os.environ.get("CHAT_RATE_PER_MIN", "10"))
CHAT_DAILY_LIMIT = int(os.environ.get("CHAT_DAILY_LIMIT", "100"))
TAGS = ["学习", "社团", "其它"]

# ---- 学校账号登录 + 白名单（实现见 arena/school_auth.py）-----------------
# SCHOOL_AUTH_ENABLED=1 时登录页出现「用学校账号登录」入口；
# 关掉它即完全回到原来的「聊天站账号」登录方式，两者互不影响。
SCHOOL_AUTH_ENABLED = os.environ.get("SCHOOL_AUTH_ENABLED", "0").strip() == "1"
# 学号映射成聊天站内部邮箱时用的域名（学生看不到这个邮箱）
SCHOOL_EMAIL_DOMAIN = os.environ.get("SCHOOL_EMAIL_DOMAIN", "stu.wfla-ailab.top").strip()
# 让登录 cookie 跨子域名（chat./tutorial.）通用；留空则只在当前域名生效
SCHOOL_COOKIE_DOMAIN = os.environ.get("SCHOOL_COOKIE_DOMAIN", "").strip()
# 平台登录 cookie 有效期（秒），默认与聊天站一致的 30 天
LOGIN_COOKIE_MAX_AGE = int(os.environ.get("LOGIN_COOKIE_MAX_AGE", str(30 * 24 * 3600)))

# 擂台可选模型。value 是写入数据库 / 传给每个供应商的真实模型标识：
#   deepseek-chat / deepseek-reasoner            → 走 DeepSeek
#   openrouter:厂商/模型                          → 走 OpenRouter
#   jiyuan:模型                                   → 走基元律动（Token Rhythm）
# label 是给社员看的中文名。provider 为空表示内置 provider。
# 每个供应商只有在对应 Key 已配置时才出现在下拉里，避免社员选到跑不通的模型。
MODELS = []
if DEEPSEEK_KEY:
    MODELS += [
        {"value": "deepseek-chat", "label": "DeepSeek-V3（快，日常问答）", "provider": "deepseek"},
        {"value": "deepseek-reasoner", "label": "DeepSeek-R1（深推理，慢但严谨）", "provider": "deepseek"},
    ]
if OPENROUTER_KEY:
    MODELS += [
        {"value": "openrouter:deepseek/deepseek-chat-v3-0324", "label": "DeepSeek-V3（经由 OpenRouter）", "provider": "openrouter"},
        {"value": "openrouter:anthropic/claude-3.5-sonnet", "label": "Claude 3.5 Sonnet（OpenRouter）", "provider": "openrouter"},
        {"value": "openrouter:openai/gpt-4o-mini", "label": "GPT-4o mini（OpenRouter）", "provider": "openrouter"},
    ]
if JIYUAN_KEY:
    MODELS += [
        {"value": "jiyuan:glm-5.2", "label": "GLM-5.2（基元律动）", "provider": "jiyuan"},
        {"value": "jiyuan:deepseek-v4-pro", "label": "DeepSeek-V4-Pro（基元律动）", "provider": "jiyuan"},
        {"value": "jiyuan:qwen3.7-max", "label": "Qwen3.7-Max（基元律动）", "provider": "jiyuan"},
        {"value": "jiyuan:kimi-k2.7-code", "label": "Kimi-K2.7（基元律动）", "provider": "jiyuan"},
    ]
# 允许的模型 value 集合（用于校验写入的值）
MODEL_VALUES = {m["value"] for m in MODELS}
MODEL_LABELS = {m["value"]: m["label"] for m in MODELS}

def default_model():
    """返回当前可用的默认模型：优先 deepseek-chat；未配 DeepSeek 时用第一个可用模型。"""
    for v in ("deepseek-chat", "deepseek-reasoner"):
        if v in MODEL_VALUES:
            return v
    return next(iter(MODEL_VALUES), "deepseek-chat")

def provider_for(model):
    """按模型 value 判断走哪个供应商，返回 (provider, retry_404) 。"""
    if model.startswith("openrouter:"):
        return "openrouter"
    if model.startswith("jiyuan:"):
        return "jiyuan"
    return "deepseek"

WEEKLY_THEMES = [
    "学习：做一个帮你学某一科的 Agent",
    "社团：做一个介绍我们社团的 Agent",
    "创意写作：故事、诗歌、小说创作 Agent",
    "工具人：翻译、润色、文案等效率 Agent",
    "代码：编程助手或算法讲解 Agent",
    "考试：备考刷题、错题讲解 Agent",
    "生活：学习规划、时间管理、健康建议 Agent",
    "角色扮演：名人或虚拟角色的人设 Agent",
    "双语：中英互译、口语陪练 Agent",
    "点子王：头脑风暴、创意生成 Agent",
    "答辩：模拟面试、答辩演练 Agent",
    "自由主题：你最想做的任何 Agent",
]

app = Flask(__name__)
app.secret_key = SECRET
# 会话持久化：默认所有登录均为 30 天（不做「记住我」开关，逻辑保持简单）。
# 注意：SESSION_COOKIE_SECURE=True 要求 https 访问（线上部署为 https，正常生效）；
# 本地用 http 调试时浏览器会拒绝带 Secure 标记的 cookie，表现为登录不上，
# 需临时把该项改为 False 或改走 https 才能在本地验证登录。
# SESSION_COOKIE_SAMESITE=Lax 保证从聊天站链接跳转 /arena 时会带上 arena 自己的 session cookie。
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
)


@app.context_processor
def inject_models():
    """全局注入擂台可选模型，供所有模板的模型下拉使用（create / pipeline_builder 等）。"""
    return {"models": MODELS}

# 挂到聊天站同域名的子路径（BASE_PATH），使其能读到聊天站的登录 cookie。
if BASE_PATH and BASE_PATH != "/":
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    from werkzeug.wrappers import Response as _WerkzeugResponse

    app.wsgi_app = DispatcherMiddleware(
        _WerkzeugResponse("Not Found", status=404), {BASE_PATH: app.wsgi_app}
    )


@app.template_filter("fromjson")
def fromjson(value):
    try:
        return json.loads(value) if value else {}
    except Exception:
        return {}


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def week_key():
    year, week, _ = datetime.date.today().isocalendar()
    return f"{year}-W{week}"


def current_theme():
    d = db()
    row = d.execute("SELECT value FROM settings WHERE key='theme_override'").fetchone()
    if row and row["value"]:
        return row["value"]
    week_num = datetime.date.today().isocalendar()[1]
    return WEEKLY_THEMES[week_num % len(WEEKLY_THEMES)]


def current_notice():
    """站内公告：存在 settings 表里（照 theme_override 的读写方式）。

    返回去掉两端空白的字符串；空字符串 = 没有公告，导航下方不显示横幅。
    社长在 /admin/stats 页面里写。
    """
    try:
        row = db().execute("SELECT value FROM settings WHERE key='notice'").fetchone()
    except Exception:  # noqa: BLE001 —— 任何页面都不该因为公告读失败而 500
        return ""
    return (row["value"] or "").strip() if row else ""


@app.context_processor
def inject_site_links():
    """给所有模板注入教程站域名（导航条「教程站」链接用）、统一登录开关与站内公告。"""
    return {
        "tutorial_domain": os.environ.get("TUTORIAL_DOMAIN", "tutorial.wfla-ailab.top"),
        "school_auth_enabled": SCHOOL_AUTH_ENABLED,
        "notice": current_notice(),
    }


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
def db():
    if "db" in g:
        return g.db
    g.db = sqlite3.connect(DB_PATH)
    g.db.row_factory = sqlite3.Row
    g.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT);
        CREATE TABLE IF NOT EXISTS agents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            system TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT 'deepseek-chat',
            tag TEXT NOT NULL DEFAULT '其它',
            params TEXT NOT NULL DEFAULT '{}',
            author_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT,
            test_result TEXT DEFAULT '',
            tested_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS votes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            agent_id INTEGER NOT NULL,
            week TEXT NOT NULL,
            created_at TEXT,
            UNIQUE(user_id, agent_id, week));
        CREATE TABLE IF NOT EXISTS favorites(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            agent_id INTEGER NOT NULL,
            created_at TEXT,
            UNIQUE(user_id, agent_id));
        CREATE TABLE IF NOT EXISTS comments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT);
        CREATE TABLE IF NOT EXISTS chat_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT);
        CREATE TABLE IF NOT EXISTS pipelines(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            author_id INTEGER NOT NULL,
            is_shared INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT);
        CREATE TABLE IF NOT EXISTS pipeline_steps(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT 'deepseek-chat',
            system TEXT NOT NULL DEFAULT '',
            instruction TEXT NOT NULL DEFAULT '',
            temperature TEXT DEFAULT '0.7',
            max_tokens TEXT DEFAULT '2048');
        CREATE TABLE IF NOT EXISTS pipeline_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            input TEXT NOT NULL,
            stages TEXT NOT NULL DEFAULT '[]',
            created_at TEXT);
        CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            repo_url TEXT,
            demo_url TEXT DEFAULT '',
            display_name TEXT,
            description TEXT DEFAULT '',
            tag TEXT DEFAULT '其它',
            github_owner TEXT,
            github_repo TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT);
        """
    )
    # 迁移：老库补列
    cols = {r["name"] for r in g.db.execute("PRAGMA table_info(agents)")}
    if "test_result" not in cols:
        g.db.execute("ALTER TABLE agents ADD COLUMN test_result TEXT DEFAULT ''")
    if "tested_at" not in cols:
        g.db.execute("ALTER TABLE agents ADD COLUMN tested_at TEXT DEFAULT ''")
    pstep_cols = {r["name"] for r in g.db.execute("PRAGMA table_info(pipeline_steps)")}
    if "agent_id" not in pstep_cols:
        g.db.execute("ALTER TABLE pipeline_steps ADD COLUMN agent_id INTEGER")
    # 统一登录相关表（白名单 / 内部账号映射 / 开通申请）
    school_auth.ensure_tables(g.db)
    g.db.commit()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()


# --------------------------------------------------------------------------
# HTTP 小工具
# --------------------------------------------------------------------------
def http(method, url, payload=None, token=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


# --------------------------------------------------------------------------
# Open WebUI 交互
# --------------------------------------------------------------------------
def owu_signin(email, password):
    status, body = http(
        "POST",
        f"{OPENWEBUI_URL}/api/v1/auths/signin",
        {"email": email, "password": password},
    )
    if status == 200 and isinstance(body, dict) and body.get("token"):
        return body["token"]
    return None


def owu_self(token):
    # v0.11 的当前用户接口：GET /api/v1/auths/（带斜杠）
    status, body = http("GET", f"{OPENWEBUI_URL}/api/v1/auths/", token=token)
    if status == 200 and isinstance(body, dict) and body.get("email"):
        return body
    return None


def owu_admin_token():
    return owu_signin(ADMIN_EMAIL, ADMIN_PASSWORD)


def owu_find_user_id(token, email):
    """管理员接口：按邮箱找到聊天站用户 id（找不到返回 None）。"""
    status, body = http("GET", f"{OPENWEBUI_URL}/api/v1/users/", token=token)
    if status != 200 or not isinstance(body, dict):
        return None
    users = body.get("users") if isinstance(body.get("users"), list) else body
    if not isinstance(users, list):
        return None
    target = (email or "").lower()
    for u in users:
        if isinstance(u, dict) and str(u.get("email", "")).lower() == target:
            return u.get("id")
    return None


def owu_create_model(token, model_id, name, description, system, model, tag):
    payload = {
        "id": model_id,
        "name": name,
        "meta": {
            "description": description,
            "capabilities": {"vision": False, "usage": True},
            "tags": [tag],
        },
        "params": {"system": system, "model": model},
    }
    return http("POST", f"{OPENWEBUI_URL}/api/v1/models/create", payload, token=token)


# --------------------------------------------------------------------------
# 模型试聊：按 model 前缀路由到对应供应商（deepseek / openrouter / jiyuan）。
# --------------------------------------------------------------------------
def _chat_completions(url, payload, key):
    """向 OpenAI 兼容端点发一次非流式请求，返回 (status, body_dict)。"""
    status, body = http(
        "POST", f"{url}/chat/completions", payload, token=key, timeout=180
    )
    if not isinstance(body, dict):
        # OpenRouter 等返回 JSON；若上游给出纯文本错误也转交给调用方
        try:
            body = json.loads(body)
        except Exception:
            pass
    return status, body


def model_chat(model, system, history, temperature=0.7, top_p=1.0, max_tokens=2048):
    """按 model 前缀路由到对应供应商发起对话。返回模型回复文本（失败时含错误说明）。"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages += history[-12:]

    provider = provider_for(model)
    if provider == "openrouter":
        if not OPENROUTER_KEY:
            return "（未配置 OPENROUTER_API_KEY，无法调用）"
        # OpenRouter 官方约定：自定义名需走 "model" 提示，且 base_url 下不剥前缀
        api_model = model.split("openrouter:", 1)[1]
        url = OPENROUTER_URL
        key = OPENROUTER_KEY
    elif provider == "jiyuan":
        if not JIYUAN_KEY:
            return "（未配置 JIYUAN_API_KEY，无法调用）"
        api_model = model.split("jiyuan:", 1)[1]
        url = JIYUAN_URL
        key = JIYUAN_KEY
    else:
        api_model = model
        url = DEEPSEEK_URL
        key = DEEPSEEK_KEY

    payload = {"model": api_model, "messages": messages, "stream": False}
    if api_model != "deepseek-reasoner":
        payload["temperature"] = float(temperature)
        payload["top_p"] = float(top_p)
    payload["max_tokens"] = int(max_tokens)

    status, body = _chat_completions(url, payload, key)
    if status == 200 and isinstance(body, dict):
        try:
            msg = body["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            # reasoner 长输入时可能把预算都花在推理上、正文为空 → 回退用推理内容
            if not content and msg.get("reasoning_content"):
                content = "（模型只返回了推理过程）\n\n" + (msg.get("reasoning_content") or "").strip()
            return content
        except Exception:
            return "（解析响应失败）"
    detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:300]
    return f"（调用失败 HTTP {status}: {detail}）"


# 兼容旧名：旧代码把入口叫 deepseek_chat。保留别名减小改动面。
def deepseek_chat(model, system, history, temperature=0.7, top_p=1.0, max_tokens=2048):
    return model_chat(model, system, history, temperature, top_p, max_tokens)


def auto_test(agent_row):
    """用 Agent 的示例问题自动试跑一次，返回 (结果, 时间)。"""
    params = json.loads(agent_row["params"] or "{}")
    example = (params.get("example") or "").strip()
    if not example:
        return "（未填示例问题，跳过自动试跑）", now()
    _prov = provider_for(agent_row["model"] or "deepseek-chat")
    _key = DEEPSEEK_KEY if _prov == "deepseek" else (OPENROUTER_KEY if _prov == "openrouter" else JIYUAN_KEY)
    if not _key:
        return f"（模型所属供应商未配置 Key，无法试跑）", now()
    reply = deepseek_chat(
        agent_row["model"], agent_row["system"],
        [{"role": "user", "content": example}],
        params.get("temperature", 0.7), params.get("top_p", 1.0),
        min(int(params.get("max_tokens", 2048)), 600),
    )
    return reply, now()


# --------------------------------------------------------------------------
# 用户 / 权限 / 计数
# --------------------------------------------------------------------------
def current_user():
    uid = session.get("uid")
    if uid:
        row = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row:
            return row
    # 复用聊天站的登录 cookie（同域名 /arena 下浏览器会自动带上 token）：
    # 首次访问时用它免登录建会话，之后走 arena 自己的 session。
    token = request.cookies.get("token")
    if token:
        info = owu_self(token)
        email = (info or {}).get("email")
        if email:
            email = email.lower()
            d = db()
            d.execute(
                "INSERT INTO users(email, name, is_admin, created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(email) DO UPDATE SET name=excluded.name",
                (email, (info or {}).get("name") or email.split("@")[0],
                 1 if email == ADMIN_EMAIL else 0, now()),
            )
            d.commit()
            row = d.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            session["uid"] = row["id"]
            # 借聊天站 cookie 自动建档登录的会话同样持久化 30 天
            session.permanent = True
            return row
    return None


def chat_allowed(user_id):
    """试聊限流：返回 (是否允许, 提示信息)。"""
    d = db()
    today = datetime.date.today().isoformat()
    daily = d.execute(
        "SELECT COUNT(*) AS c FROM chat_log WHERE user_id=? AND date(created_at)=?",
        (user_id, today),
    ).fetchone()["c"]
    if daily >= CHAT_DAILY_LIMIT:
        return False, f"今日试聊已达 {CHAT_DAILY_LIMIT} 条上限，明天再来吧"
    cutoff = (datetime.datetime.now() - datetime.timedelta(minutes=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    recent = d.execute(
        "SELECT COUNT(*) AS c FROM chat_log WHERE user_id=? AND created_at>=?",
        (user_id, cutoff),
    ).fetchone()["c"]
    if recent >= CHAT_RATE_PER_MIN:
        return False, f"操作太快啦，每分钟最多 {CHAT_RATE_PER_MIN} 条，稍等几秒再试"
    return True, ""


def slugify(text):
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text.strip().lower())
    return slug.strip("-")[:40] or "agent"


# --------------------------------------------------------------------------
# 页面路由
# --------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    # 已借聊天站 cookie 登录 → 直接进擂台
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        token = owu_signin(email, password)
        if not token:
            return render_template("login.html", error="账号或密码错误（请使用聊天站的账号）")
        info = owu_self(token) or {}
        d = db()
        d.execute(
            "INSERT INTO users(email, name, is_admin, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET name=excluded.name",
            (email, info.get("name") or email.split("@")[0],
             1 if email == ADMIN_EMAIL else 0, now()),
        )
        d.commit()
        row = d.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        session["uid"] = row["id"]
        # 表单登录成功后同样持久化 30 天
        session.permanent = True
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# 学校账号统一登录（白名单制）
# --------------------------------------------------------------------------
def school_cookie_domain():
    """登录 cookie 的域：默认跟随请求域名，让 chat./tutorial. 共用一个登录态。"""
    if SCHOOL_COOKIE_DOMAIN:
        return SCHOOL_COOKIE_DOMAIN
    host = (request.host or "").split(":")[0]
    if host == "wfla-ailab.top" or host.endswith(".wfla-ailab.top"):
        return ".wfla-ailab.top"
    return None


def owu_provision(code, name):
    """为通过学校认证的社员准备聊天站内部账号。

    返回 (email, password, error)；error 非空表示失败。
    账号已存在就复用本地记录的随机密码；不存在就用管理员权限新建。
    """
    email = school_auth.owu_email_for(code, SCHOOL_EMAIL_DOMAIN)
    d = db()
    row = d.execute("SELECT * FROM school_accounts WHERE code=?", (code,)).fetchone()
    if row and owu_signin(row["owu_email"], row["owu_password"]):
        return row["owu_email"], row["owu_password"], ""

    admin = owu_admin_token()
    if not admin:
        return None, None, "聊天站管理员凭据不可用，暂时无法开通账号，请联系社长"

    password = secrets.token_urlsafe(18)
    status, body = http(
        "POST",
        f"{OPENWEBUI_URL}/api/v1/auths/add",
        {"name": name or code, "email": email, "password": password, "role": "user"},
        token=admin,
    )
    if status != 200:
        # 该邮箱早先被手动建过 → 用管理员接口把密码重置成我们的随机密码
        detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        user_id = owu_find_user_id(admin, email)
        if not user_id:
            return None, None, f"聊天站账号开通失败：{str(detail)[:120]}"
        status2, body2 = http(
            "POST",
            f"{OPENWEBUI_URL}/api/v1/users/{user_id}/update",
            {"password": password},
            token=admin,
        )
        if status2 != 200:
            detail2 = body2 if isinstance(body2, str) else json.dumps(body2, ensure_ascii=False)
            return None, None, f"聊天站账号密码重置失败：{str(detail2)[:120]}"

    d.execute(
        "INSERT INTO school_accounts(code, owu_email, owu_password, created_at, last_login) "
        "VALUES(?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET owu_email=excluded.owu_email,"
        " owu_password=excluded.owu_password, last_login=excluded.last_login",
        (code, email, password, now(), now()),
    )
    d.commit()
    return email, password, ""


@app.route("/school-login", methods=["GET", "POST"])
def school_login():
    """用学校综合系统账号登录社团平台（只有白名单里的账号能进）。

    流程：学校系统校验密码 → 查白名单 → 开通/复用聊天站内部账号 → 建立会话。
    学生的学校密码只在第一步用一次，不落库、不写日志、不进 session。
    """
    if not SCHOOL_AUTH_ENABLED:
        return redirect(url_for("login"))
    if current_user():
        return redirect(url_for("index"))
    if request.method != "POST":
        return render_template("school_login.html", error="", code="", pending=False)

    result = school_auth.verify(request.form.get("code", ""), request.form.get("password", ""))
    norm = school_auth.normalize_code(request.form.get("code", ""))
    if not result["ok"]:
        return render_template("school_login.html", error=result["message"],
                               code=norm, pending=False)

    d = db()
    entry = school_auth.is_allowed(d, norm)
    if not entry:
        # 账号是真的，但还没开通：留档，等社长在后台加白名单
        school_auth.record_request(d, norm, request.form.get("name", "").strip(),
                                   request.form.get("message", "").strip(), now())
        return render_template(
            "school_login.html", code=norm, pending=True,
            error="学校账号验证通过，但这个账号还没有开通社团平台权限（白名单制）。"
                  "申请已记录，社长开通后即可登录。",
        )

    email, owu_password, err = owu_provision(norm, entry["name"] or norm)
    if err:
        return render_template("school_login.html", error=err, code=norm, pending=False)

    token = owu_signin(email, owu_password)
    if not token:
        return render_template("school_login.html", error="聊天站登录失败，请联系社长",
                               code=norm, pending=False)

    # 白名单里勾了「管理员权限」（allowlist.role='admin'）要真的生效：
    # 此前这里只比较 ADMIN_EMAIL，导致后台那个勾选框勾了没用（已由测试覆盖）。
    is_admin = 1 if (email == ADMIN_EMAIL or (entry["role"] or "member") == "admin") else 0
    d.execute(
        "INSERT INTO users(email, name, is_admin, created_at) VALUES(?,?,?,?) "
        "ON CONFLICT(email) DO UPDATE SET name=excluded.name, is_admin=excluded.is_admin",
        (email, entry["name"] or norm, is_admin, now()),
    )
    d.commit()
    row = d.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    session["uid"] = row["id"]
    session.permanent = True

    resp = make_response(redirect(url_for("index")))
    # 顺手种上聊天站的登录 cookie，于是聊天站/擂台/教程站是同一个登录态。
    # 不设 httponly：聊天站前端需要读这个 cookie（与聊天站自身行为保持一致）。
    resp.set_cookie(
        "token", token, max_age=LOGIN_COOKIE_MAX_AGE, domain=school_cookie_domain(),
        path="/", samesite="Lax", secure=bool(request.is_secure), httponly=False,
    )
    return resp


@app.route("/admin/allowlist", methods=["GET", "POST"])
def admin_allowlist():
    """社长后台：手动维护白名单（粘贴学号批量加 / 停用 / 删除 / 看申请）。"""
    user = current_user()
    if not user or not user["is_admin"]:
        return redirect(url_for("login"))

    d = db()
    message = ""
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "add":
            entries = school_auth.parse_bulk(request.form.get("codes", ""))
            if entries:
                added, updated = school_auth.add_entries(
                    d, entries, now(),
                    role="admin" if request.form.get("as_admin") else "member",
                    note=request.form.get("note", "").strip(),
                )
                message = f"已处理 {len(entries)} 个账号：新增 {added} 个，更新 {updated} 个"
            else:
                message = "没有解析到任何学号，请一行写一个"
        elif action == "remove":
            n = school_auth.remove_entry(d, request.form.get("code", ""))
            message = f"已从白名单删除 {n} 个账号"
        elif action == "enable":
            n = school_auth.set_active(d, request.form.get("code", ""), True)
            message = f"已启用 {n} 个账号"
        elif action == "disable":
            n = school_auth.set_active(d, request.form.get("code", ""), False)
            message = f"已停用 {n} 个账号"
        else:
            message = "未知操作"

    return render_template(
        "admin_allowlist.html", user=user, message=message,
        entries=school_auth.list_entries(d), requests=school_auth.pending_requests(d),
        school_auth_enabled=SCHOOL_AUTH_ENABLED,
    )


@app.route("/guide")
def guide():
    user = current_user()
    return render_template("guide.html", user=user)


@app.route("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    tag = request.args.get("tag", "")
    query = (
        "SELECT a.*, u.name AS author_name FROM agents a "
        "JOIN users u ON u.id = a.author_id"
    )
    args = []
    if tag:
        query += " WHERE a.tag = ?"
        args.append(tag)
    query += " ORDER BY a.created_at DESC"
    rows = db().execute(query, args).fetchall()
    my_favs = {
        r["agent_id"]
        for r in db().execute(
            "SELECT agent_id FROM favorites WHERE user_id=?", (user["id"],)
        ).fetchall()
    }
    comments = {}
    for r in db().execute(
        "SELECT c.*, u.name AS user_name FROM comments c "
        "JOIN users u ON u.id = c.user_id ORDER BY c.created_at ASC"
    ).fetchall():
        comments.setdefault(r["agent_id"], []).append(r)
    return render_template(
        "index.html",
        user=user,
        agents=rows,
        my_favs=my_favs,
        comments=comments,
        tag=tag,
        tags=TAGS,
        theme=current_theme(),
        chat_rate=CHAT_RATE_PER_MIN,
        chat_daily=CHAT_DAILY_LIMIT,
    )


@app.route("/create", methods=["GET", "POST"])
def create():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    form = dict(request.form) if request.method == "POST" else {}
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        system = request.form.get("system", "").strip()
        if not name or not system:
            return render_template(
                "create.html", user=user, form=form, tags=TAGS, models=MODELS,
                theme=current_theme(),
                error="名称和系统提示词不能为空",
            )
        model = request.form.get("model", default_model())
        # 模型校验：只允许预置列表里的 value，防止注入任意模型串或乱用未配 Key 的供应商。
        if model not in MODEL_VALUES:
            if not MODEL_VALUES:
                return render_template("create.html", user=user, form=form, tags=TAGS, models=MODELS,
                                       theme=current_theme(), error="当前没有可用模型（未配置任何供应商 Key）")
            model = default_model()
        tag = request.form.get("tag", "其它")
        model_id = slugify(name) + "-" + secrets.token_hex(3)
        params = {
            "temperature": request.form.get("temperature", "0.7"),
            "max_tokens": request.form.get("max_tokens", "2048"),
            "top_p": request.form.get("top_p", "1"),
            "example": request.form.get("example", ""),
        }
        note = ""
        admin_token = owu_admin_token()
        if admin_token:
            status, body = owu_create_model(
                admin_token, model_id, name, request.form.get("description", ""),
                system, model, tag,
            )
            if status != 200:
                detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:150]
                note = f"（同步到主站失败 HTTP {status}: {detail}）"
        else:
            note = "（管理员凭据无效，未能同步到主站）"
        d = db()
        cur = d.execute(
            "INSERT INTO agents(model_id,name,description,system,model,tag,params,author_id,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (model_id, name, request.form.get("description", ""), system, model, tag,
             json.dumps(params, ensure_ascii=False), user["id"], "pending", now()),
        )
        row = d.execute("SELECT * FROM agents WHERE id=?", (cur.lastrowid,)).fetchone()
        test_result, tested_at = auto_test(row)
        d.execute("UPDATE agents SET test_result=?, tested_at=? WHERE id=?",
                  (test_result, tested_at, row["id"]))
        d.commit()
        return render_template(
            "create.html", user=user, form={}, tags=TAGS, models=MODELS,
            theme=current_theme(),
            success=f"提交成功！{note} 等待管理员审核后即可上架擂台。",
            test_result=test_result, tested_at=tested_at,
        )
    return render_template("create.html", user=user, form=form, tags=TAGS,
                           models=MODELS, theme=current_theme())


@app.route("/mine")
def mine():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    mine_rows = db().execute(
        "SELECT a.*, (SELECT COUNT(*) FROM votes v WHERE v.agent_id = a.id) AS stars "
        "FROM agents a WHERE a.author_id=? ORDER BY a.created_at DESC",
        (user["id"],),
    ).fetchall()
    fav_rows = db().execute(
        "SELECT a.*, u.name AS author_name, "
        "(SELECT COUNT(*) FROM votes v WHERE v.agent_id = a.id) AS stars "
        "FROM favorites f JOIN agents a ON a.id = f.agent_id "
        "JOIN users u ON u.id = a.author_id "
        "WHERE f.user_id=? ORDER BY f.created_at DESC",
        (user["id"],),
    ).fetchall()
    my_comments = db().execute(
        "SELECT c.*, u.name AS user_name, a.name AS agent_name "
        "FROM comments c JOIN agents a ON a.id = c.agent_id "
        "JOIN users u ON u.id = c.user_id "
        "WHERE a.author_id=? ORDER BY c.created_at DESC",
        (user["id"],),
    ).fetchall()
    return render_template("mine.html", user=user, mine_rows=mine_rows,
                           fav_rows=fav_rows, my_comments=my_comments)


# --------------------------------------------------------------------------
# 流水线（Agent 流）：把多个 Agent 串成一条处理流程，共享给成员使用
# --------------------------------------------------------------------------
def run_pipeline_exec(pipeline_id, user_id, input_text):
    """按顺序执行流水线各步骤，返回 (ok, stages, error)。

    数据流：原始输入与每步输出累积成 ctx；每步发给模型的 prompt = 指令 + 累积 ctx（逻辑不变）。
    步骤是 Agent 引用：agent_id 非空且 Agent 仍在 → 用该 Agent 当前的名称/模型/系统提示词；
    否则（agent_id 为空或 Agent 已删除）回退到步骤保存的快照。
    每步 stage 记录 {step(显示名), agent_id, input(发给模型的完整 prompt), output}，数据流全程可见。
    """
    d = db()
    steps = d.execute(
        "SELECT * FROM pipeline_steps WHERE pipeline_id=? ORDER BY position ASC",
        (pipeline_id,),
    ).fetchall()
    if not steps:
        return False, [], "流水线没有步骤"
    ctx = "原始输入：\n" + input_text.strip()
    stages = []
    for i, s in enumerate(steps):
        agent_id = s["agent_id"]
        display_name = s["name"]
        model = s["model"]
        system = s["system"]
        if agent_id:
            agent = d.execute(
                "SELECT name, model, system FROM agents WHERE id=?", (agent_id,)
            ).fetchone()
            if agent:
                display_name = agent["name"]
                model = agent["model"]
                system = agent["system"]
        prompt = f"{s['instruction'] or '请处理以下内容'}\n\n{ctx}"
        reply = deepseek_chat(
            model, system,
            [{"role": "user", "content": prompt}],
            float(s["temperature"] or 0.7), 1.0,
            int(s["max_tokens"] or 2048),
        )
        stages.append({
            "step": display_name,
            "agent_id": agent_id,
            "input": prompt,
            "output": reply,
        })
        ctx += f"\n\n--- 第 {i + 1} 步「{display_name}」的输出 ---\n" + reply
    return True, stages, ""


def ensure_demo_pipeline():
    """首次访问流水线页时，给管理员播种一个示例流水线（生成→评审→降AI率）。"""
    d = db()
    if d.execute("SELECT value FROM settings WHERE key='demo_pipeline_seeded'").fetchone():
        return
    admin = d.execute("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1").fetchone()
    if not admin:
        return
    now_s = now()
    cur = d.execute(
        "INSERT INTO pipelines(name, description, author_id, is_shared, created_at, updated_at) "
        "VALUES(?,?,?,1,?,?)",
        ("文章生成流水线",
         "示例：初步生成 → 专业评审 → 降 AI 率。输入主题即可一键产出经过多重 Agent 审查的文章。",
         admin["id"], now_s, now_s),
    )
    pid = cur.lastrowid
    demo_steps = [
        ("初步生成", "deepseek-chat",
         "你是内容创作助手，负责根据要求生成内容初稿。",
         "根据下面的输入，生成一篇结构完整的中文文章初稿（600-800 字），分 3 个小节。不要使用 emoji。"),
        ("专业评审", "deepseek-reasoner",
         "你是一位严格的评审专家，善于发现内容中的问题。",
         "以上一步生成的内容为基础，逐条指出存在的问题（逻辑漏洞、事实存疑、表述生硬、结构问题等），并给出具体修改建议。不要使用 emoji。"),
        ("审查兜底（降 AI 率）", "deepseek-chat",
         "你是文字润色专家，擅长把书面化、模板化的表达改得自然、像真人手写。",
         "根据评审意见重写上一步的文本：表达自然流畅，去掉明显的 AI 腔（如「首先其次最后」「总而言之」「值得一提的是」等套话），保持原意，输出修改后的完整版本。不要使用 emoji。"),
    ]
    for pos, (nm, model, system, instruction) in enumerate(demo_steps, 1):
        # reasoner 需要更大的输出预算（推理会消耗大量 token）
        step_tokens = "4096" if model == "deepseek-reasoner" else "2048"
        d.execute(
            "INSERT INTO pipeline_steps(pipeline_id, position, name, model, system, "
            "instruction, temperature, max_tokens) VALUES(?,?,?,?,?,?,?,?)",
            (pid, pos, nm, model, system, instruction, "0.7", step_tokens),
        )
    d.execute("INSERT INTO settings(key, value) VALUES('demo_pipeline_seeded','1')")
    d.commit()


@app.route("/pipelines")
def pipelines():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    ensure_demo_pipeline()
    mine = db().execute(
        "SELECT p.*, u.name AS author_name, "
        "(SELECT COUNT(*) FROM pipeline_steps s WHERE s.pipeline_id=p.id) AS step_count "
        "FROM pipelines p JOIN users u ON u.id=p.author_id "
        "WHERE p.author_id=? ORDER BY p.updated_at DESC",
        (user["id"],),
    ).fetchall()
    shared = db().execute(
        "SELECT p.*, u.name AS author_name, "
        "(SELECT COUNT(*) FROM pipeline_steps s WHERE s.pipeline_id=p.id) AS step_count "
        "FROM pipelines p JOIN users u ON u.id=p.author_id "
        "WHERE p.is_shared=1 AND p.author_id!=? ORDER BY p.updated_at DESC",
        (user["id"],),
    ).fetchall()
    return render_template("pipelines.html", user=user, mine=mine, shared=shared)


def parse_step_form():
    """从表单读各步骤（含 Agent 引用），返回 [(agent_id, name, model, system, instruction, temp, tokens), ...]。

    agent_id 为空（自定义步骤）时为 None；否则是选中的 Agent id（int）。
    """
    step_names = request.form.getlist("step_name")
    step_models = request.form.getlist("step_model")
    step_systems = request.form.getlist("step_system")
    step_instructions = request.form.getlist("step_instruction")
    step_temps = request.form.getlist("step_temperature")
    step_tokens = request.form.getlist("step_max_tokens")
    step_agent_ids = request.form.getlist("step_agent_id")
    valid = []
    for i in range(len(step_names)):
        nm = (step_names[i] if i < len(step_names) else "").strip()
        if not nm:
            continue
        aid_raw = (step_agent_ids[i] if i < len(step_agent_ids) else "").strip()
        agent_id = int(aid_raw) if aid_raw.isdigit() else None
        valid.append((
            agent_id,
            nm,
            (step_models[i] if i < len(step_models) else default_model()).strip() or default_model(),
            (step_systems[i] if i < len(step_systems) else "").strip(),
            (step_instructions[i] if i < len(step_instructions) else "").strip(),
            (step_temps[i] if i < len(step_temps) else "0.7").strip() or "0.7",
            (step_tokens[i] if i < len(step_tokens) else "2048").strip() or "2048",
        ))
    return valid


def insert_steps(d, pipeline_id, steps):
    """清空旧步骤并写入新步骤（含 agent_id 引用）。"""
    d.execute("DELETE FROM pipeline_steps WHERE pipeline_id=?", (pipeline_id,))
    for pos, (agent_id, nm, model, system, instruction, temp, tokens) in enumerate(steps, 1):
        d.execute(
            "INSERT INTO pipeline_steps(pipeline_id, position, agent_id, name, model, system, "
            "instruction, temperature, max_tokens) VALUES(?,?,?,?,?,?,?,?,?)",
            (pipeline_id, pos, agent_id, nm, model, system, instruction, temp, tokens),
        )


@app.route("/pipeline/new", methods=["GET", "POST"])
def pipeline_new():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    agents = db().execute(
        "SELECT * FROM agents ORDER BY created_at DESC"
    ).fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name:
            return render_template("pipeline_builder.html", user=user, agents=agents,
                                   pipeline=None, editing=False, steps_data=[],
                                   error="流水线名称不能为空")
        valid_steps = parse_step_form()
        if not valid_steps:
            return render_template("pipeline_builder.html", user=user, agents=agents,
                                   pipeline=None, editing=False, steps_data=[],
                                   error="至少需要一个步骤")
        d = db()
        now_s = now()
        cur = d.execute(
            "INSERT INTO pipelines(name, description, author_id, is_shared, created_at, updated_at) "
            "VALUES(?,?,?,0,?,?)",
            (name, description, user["id"], now_s, now_s),
        )
        pid = cur.lastrowid
        insert_steps(d, pid, valid_steps)
        d.commit()
        return redirect(url_for("pipeline_detail", pipeline_id=pid))
    return render_template("pipeline_builder.html", user=user, agents=agents,
                           pipeline=None, editing=False, steps_data=[])


@app.route("/pipeline/<int:pipeline_id>/edit", methods=["GET", "POST"])
def pipeline_edit(pipeline_id):
    """编辑流水线（仅作者或管理员）：改名称/说明/步骤（含重新选择 Agent）。"""
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    p = db().execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    if not p:
        return redirect(url_for("pipelines"))
    if p["author_id"] != user["id"] and not user["is_admin"]:
        return redirect(url_for("pipelines"))
    agents = db().execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
    steps = db().execute(
        "SELECT * FROM pipeline_steps WHERE pipeline_id=? ORDER BY position ASC",
        (pipeline_id,),
    ).fetchall()
    steps_data = [
        {
            "agent_id": s["agent_id"],
            "name": s["name"],
            "model": s["model"],
            "system": s["system"],
            "instruction": s["instruction"],
            "temperature": s["temperature"],
            "max_tokens": s["max_tokens"],
        }
        for s in steps
    ]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name:
            return render_template("pipeline_builder.html", user=user, agents=agents,
                                   pipeline=p, steps=steps, steps_data=steps_data,
                                   editing=True, error="流水线名称不能为空")
        valid_steps = parse_step_form()
        if not valid_steps:
            return render_template("pipeline_builder.html", user=user, agents=agents,
                                   pipeline=p, steps=steps, steps_data=steps_data,
                                   editing=True, error="至少需要一个步骤")
        d = db()
        now_s = now()
        d.execute("UPDATE pipelines SET name=?, description=?, updated_at=? WHERE id=?",
                  (name, description, now_s, p["id"]))
        insert_steps(d, p["id"], valid_steps)
        d.commit()
        return redirect(url_for("pipeline_detail", pipeline_id=p["id"]))
    return render_template("pipeline_builder.html", user=user, agents=agents,
                           pipeline=p, steps=steps, steps_data=steps_data, editing=True)


@app.route("/pipeline/<int:pipeline_id>")
def pipeline_detail(pipeline_id):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    p = db().execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    if not p or (p["author_id"] != user["id"] and not p["is_shared"] and not user["is_admin"]):
        return redirect(url_for("pipelines"))
    steps = db().execute(
        "SELECT s.*, a.name AS agent_name, a.model AS agent_model, a.system AS agent_system, "
        "a.status AS agent_status "
        "FROM pipeline_steps s LEFT JOIN agents a ON a.id = s.agent_id "
        "WHERE s.pipeline_id=? ORDER BY s.position ASC",
        (pipeline_id,),
    ).fetchall()
    runs = db().execute(
        "SELECT * FROM pipeline_runs WHERE pipeline_id=? ORDER BY created_at DESC LIMIT 10",
        (pipeline_id,),
    ).fetchall()
    runs_decoded = []
    for r in runs:
        try:
            runs_decoded.append({
                "id": r["id"],
                "input": r["input"],
                "stages": json.loads(r["stages"] or "[]"),
                "created_at": r["created_at"],
            })
        except Exception:
            pass
    author = db().execute("SELECT name FROM users WHERE id=?", (p["author_id"],)).fetchone()
    return render_template(
        "pipeline_detail.html", user=user, pipeline=p, steps=steps,
        runs=runs_decoded, author_name=author["name"] if author else "",
        is_owner=p["author_id"] == user["id"] or user["is_admin"],
    )


@app.route("/pipeline/<int:pipeline_id>/copy", methods=["POST"])
def pipeline_copy(pipeline_id):
    """把可见的流水线复制一份到自己名下（深拷贝，改副本不影响源）。

    可见性跟详情页一致：本人的 / 已共享的 / 管理员可见的，才能复制。
    副本一律 is_shared=0，避免"复制出来的东西自动又共享出去"。
    """
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    d = db()
    p = d.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    if not p or (p["author_id"] != user["id"] and not p["is_shared"] and not user["is_admin"]):
        return redirect(url_for("pipelines"))
    steps = d.execute(
        "SELECT * FROM pipeline_steps WHERE pipeline_id=? ORDER BY position ASC",
        (pipeline_id,),
    ).fetchall()
    now_s = now()
    cur = d.execute(
        "INSERT INTO pipelines(name, description, author_id, is_shared, created_at, updated_at) "
        "VALUES(?,?,?,0,?,?)",
        (f"{p['name']}（副本）", p["description"] or "", user["id"], now_s, now_s),
    )
    new_id = cur.lastrowid
    for s in steps:
        d.execute(
            "INSERT INTO pipeline_steps(pipeline_id, position, agent_id, name, model, system, "
            "instruction, temperature, max_tokens) VALUES(?,?,?,?,?,?,?,?,?)",
            (new_id, s["position"], s["agent_id"], s["name"], s["model"], s["system"],
             s["instruction"], s["temperature"], s["max_tokens"]),
        )
    d.commit()
    return redirect(url_for("pipeline_detail", pipeline_id=new_id))


@app.route("/api/pipeline/share", methods=["POST"])
def api_pipeline_share():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    p = db().execute("SELECT * FROM pipelines WHERE id=?", (data.get("pipeline_id"),)).fetchone()
    if not p:
        return jsonify({"ok": False, "error": "流水线不存在"}), 404
    if p["author_id"] != user["id"] and not user["is_admin"]:
        return jsonify({"ok": False, "error": "无权限"}), 403
    shared = 1 if data.get("shared") else 0
    d = db()
    d.execute("UPDATE pipelines SET is_shared=?, updated_at=? WHERE id=?",
              (shared, now(), p["id"]))
    d.commit()
    return jsonify({"ok": True, "shared": bool(shared)})


@app.route("/api/pipeline/run", methods=["POST"])
def api_pipeline_run():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    pipeline_id = data.get("pipeline_id")
    input_text = (data.get("input") or "").strip()
    p = db().execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    if not p:
        return jsonify({"ok": False, "error": "流水线不存在"}), 404
    if p["author_id"] != user["id"] and not p["is_shared"] and not user["is_admin"]:
        return jsonify({"ok": False, "error": "无权限"}), 403
    if not input_text:
        return jsonify({"ok": False, "error": "请输入内容"}), 400
    allowed, msg = chat_allowed(user["id"])
    if not allowed:
        return jsonify({"ok": False, "error": msg}), 429
    ok, stages, err = run_pipeline_exec(pipeline_id, user["id"], input_text)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    d = db()
    d.execute("INSERT INTO pipeline_runs(pipeline_id, user_id, input, stages, created_at) "
              "VALUES(?,?,?,?,?)",
              (pipeline_id, user["id"], input_text,
               json.dumps(stages, ensure_ascii=False), now()))
    d.commit()
    return jsonify({"ok": True, "stages": stages})


# --------------------------------------------------------------------------
# GitHub 作品廊：成员提交仓库 → 管理员审核 → 公开 API 供社团网站作品墙拉取
# --------------------------------------------------------------------------
GITHUB_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/]+)")


def parse_github_repo(url):
    """从 GitHub 仓库地址提取 (owner, repo)；解析失败返回 (None, None)。

    兼容 .git 后缀、末尾斜杠以及深层路径（如 /tree/main）。
    """
    m = GITHUB_REPO_RE.search((url or "").strip())
    if not m:
        return None, None
    owner = m.group(1).strip().strip("/")
    repo = re.sub(r"\.git/?$", "", m.group(2).strip()).strip("/")
    if not owner or not repo:
        return None, None
    return owner, repo


@app.route("/projects")
def projects():
    """作品廊列表页：所有人可见已上架作品，另附自己的提交与（管理员）待审核列表。"""
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    d = db()
    approved = d.execute(
        "SELECT p.*, u.name AS author_name FROM projects p "
        "LEFT JOIN users u ON u.id = p.user_id "
        "WHERE p.status='approved' ORDER BY p.created_at DESC"
    ).fetchall()
    mine_rows = d.execute(
        "SELECT p.*, u.name AS author_name FROM projects p "
        "LEFT JOIN users u ON u.id = p.user_id "
        "WHERE p.user_id=? ORDER BY p.created_at DESC",
        (user["id"],),
    ).fetchall()
    pending = []
    if user["is_admin"]:
        pending = d.execute(
            "SELECT p.*, u.name AS author_name FROM projects p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.status='pending' ORDER BY p.created_at DESC"
        ).fetchall()
    return render_template(
        "projects.html", user=user, approved=approved,
        mine_rows=mine_rows, pending=pending, tags=TAGS,
    )


@app.route("/projects/submit", methods=["GET", "POST"])
def project_submit():
    """提交 GitHub 作品：GET 渲染表单，POST 校验后入库（status=pending）。"""
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    form = dict(request.form) if request.method == "POST" else {}
    if request.method == "POST":
        repo_url = request.form.get("repo_url", "").strip()
        demo_url = request.form.get("demo_url", "").strip()
        display_name = request.form.get("display_name", "").strip()
        description = request.form.get("description", "").strip()
        tag = request.form.get("tag", "其它")
        if tag not in TAGS:
            tag = "其它"
        if not repo_url:
            return render_template(
                "project_submit.html", user=user, form=form, tags=TAGS,
                error="GitHub 仓库地址不能为空",
            )
        owner, repo = parse_github_repo(repo_url)
        if not owner or not repo:
            return render_template(
                "project_submit.html", user=user, form=form, tags=TAGS,
                error="仓库地址格式不对，请填形如 https://github.com/用户名/仓库名 的地址",
            )
        if not display_name:
            display_name = repo  # 名称留空时默认取仓库名
        d = db()
        d.execute(
            "INSERT INTO projects(user_id, repo_url, demo_url, display_name, description, "
            "tag, github_owner, github_repo, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user["id"], repo_url, demo_url, display_name, description, tag,
             owner, repo, "pending", now()),
        )
        d.commit()
        return redirect(url_for("projects"))
    return render_template("project_submit.html", user=user, form=form, tags=TAGS)


@app.route("/projects/<int:project_id>/status", methods=["POST"])
def project_set_status(project_id):
    """管理员审核：POST /projects/<id>/status?set=approved|rejected（表单提交后跳回列表页）。"""
    user = current_user()
    if not user or not user["is_admin"]:
        return redirect(url_for("projects"))
    new_status = request.args.get("set", "")
    if new_status not in ("approved", "rejected"):
        return redirect(url_for("projects"))
    d = db()
    row = d.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return redirect(url_for("projects"))
    d.execute("UPDATE projects SET status=? WHERE id=?", (new_status, project_id))
    d.commit()
    return redirect(url_for("projects"))


@app.route("/api/projects.json")
def api_projects_json():
    """公开 API：返回已上架作品的 JSON 数组，供社团网站作品墙跨域拉取。

    服务端不调 GitHub API，前端用 owner/repo 自行拉取实时数据；
    响应带 Cache-Control 与 Access-Control-Allow-Origin，便于静态站跨域缓存。
    """
    rows = db().execute(
        "SELECT * FROM projects WHERE status='approved' ORDER BY created_at DESC"
    ).fetchall()
    generated_at = now()
    items = []
    for r in rows:
        owner = r["github_owner"] or ""
        repo = r["github_repo"] or ""
        items.append({
            "display_name": r["display_name"],
            "description": r["description"] or "",
            "tag": r["tag"] or "其它",
            "github_owner": owner,
            "github_repo": repo,
            "html_url": f"https://github.com/{owner}/{repo}",
            "demo_url": r["demo_url"] or "",
            "avatar_url": f"https://github.com/{owner}.png?size=80",
            "updated_at": generated_at,
        })
    resp = jsonify(items)
    resp.headers["Cache-Control"] = "public, max-age=300"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/admin")
def admin_page():
    user = current_user()
    if not user or not user["is_admin"]:
        return redirect(url_for("index"))
    rows = db().execute(
        "SELECT a.*, u.name AS author_name FROM agents a "
        "JOIN users u ON u.id = a.author_id ORDER BY a.created_at DESC"
    ).fetchall()
    return render_template("admin.html", user=user, agents=rows,
                           theme=current_theme())


def _scalar(d, sql, args=()):
    """取一个整数聚合值；出错一律退回 0 —— 空库/老库不该让看板 500。"""
    try:
        row = d.execute(sql, args).fetchone()
    except Exception:  # noqa: BLE001
        return 0
    if row is None:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError):
        return 0


@app.route("/admin/stats")
def admin_stats():
    """社长数据看板：把散在几个页面里的数字聚成一页（纯 SQL 聚合 + 纯 CSS 条形图）。"""
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if not user["is_admin"]:
        return redirect(url_for("index"))
    groups, todo = collect_stats(db())
    return render_template("admin_stats.html", user=user, groups=groups,
                           notice=current_notice(), todo=todo)


def collect_stats(d):
    """算出看板要用的所有数字，返回 (groups, todo)。

    单独抽成函数是为了让测试能直接断言"页面上的数字 = sqlite 手查的数字"。
    """
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.date.today().isoformat()

    todo = {
        "agents": _scalar(d, "SELECT COUNT(*) FROM agents WHERE status='pending'"),
        "projects": _scalar(d, "SELECT COUNT(*) FROM projects WHERE status='pending'"),
        "join": _scalar(d, "SELECT COUNT(*) FROM join_requests WHERE handled=0"),
    }
    groups = [
        {
            "title": "内容",
            "items": [
                {"label": "待审 Agent", "value": todo["agents"]},
                {"label": "已上架 Agent", "value": _scalar(d, "SELECT COUNT(*) FROM agents WHERE status='approved'")},
                {"label": "已拒绝 Agent", "value": _scalar(d, "SELECT COUNT(*) FROM agents WHERE status='rejected'")},
                {"label": "待审作品", "value": todo["projects"]},
                {"label": "已上架作品", "value": _scalar(d, "SELECT COUNT(*) FROM projects WHERE status='approved'")},
            ],
        },
        {
            "title": "人",
            "items": [
                {"label": "平台用户", "value": _scalar(d, "SELECT COUNT(*) FROM users")},
                {"label": "近 7 天新增用户", "value": _scalar(d, "SELECT COUNT(*) FROM users WHERE created_at>=?", (week_ago,))},
                {"label": "白名单在用", "value": _scalar(d, "SELECT COUNT(*) FROM allowlist WHERE active=1")},
                {"label": "白名单已停用", "value": _scalar(d, "SELECT COUNT(*) FROM allowlist WHERE active=0")},
                {"label": "待处理开通申请", "value": todo["join"]},
                {"label": "学校账号已开通", "value": _scalar(d, "SELECT COUNT(*) FROM school_accounts")},
            ],
        },
        {
            "title": "用量",
            "items": [
                {"label": "今日试聊", "value": _scalar(d, "SELECT COUNT(*) FROM chat_log WHERE date(created_at)=?", (today,))},
                {"label": "近 7 天试聊", "value": _scalar(d, "SELECT COUNT(*) FROM chat_log WHERE created_at>=?", (week_ago,))},
                {"label": "流水线运行总数", "value": _scalar(d, "SELECT COUNT(*) FROM pipeline_runs")},
                {"label": "近 7 天流水线运行", "value": _scalar(d, "SELECT COUNT(*) FROM pipeline_runs WHERE created_at>=?", (week_ago,))},
                {"label": "流水线总数", "value": _scalar(d, "SELECT COUNT(*) FROM pipelines")},
                {"label": "已共享流水线", "value": _scalar(d, "SELECT COUNT(*) FROM pipelines WHERE is_shared=1")},
            ],
        },
    ]
    return groups, todo


@app.route("/admin/notice", methods=["POST"])
def admin_set_notice():
    """写/清站内公告（存 settings 表；清空即删除，横幅随之消失）。"""
    user = current_user()
    if not user or not user["is_admin"]:
        return redirect(url_for("login"))
    text = (request.form.get("notice") or "").strip()[:500]
    d = db()
    if text:
        d.execute(
            "INSERT INTO settings(key, value) VALUES('notice', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (text,),
        )
    else:
        d.execute("DELETE FROM settings WHERE key='notice'")
    d.commit()
    return redirect(url_for("admin_stats"))


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------
@app.route("/api/favorite", methods=["POST"])
def api_favorite():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    agent = db().execute("SELECT * FROM agents WHERE id=?", (data.get("agent_id"),)).fetchone()
    if not agent:
        return jsonify({"ok": False, "error": "Agent 不存在"}), 404
    d = db()
    existing = d.execute(
        "SELECT id FROM favorites WHERE user_id=? AND agent_id=?",
        (user["id"], agent["id"]),
    ).fetchone()
    if existing:
        d.execute("DELETE FROM favorites WHERE id=?", (existing["id"],))
        d.commit()
        return jsonify({"ok": True, "action": "unfavorited"})
    d.execute("INSERT INTO favorites(user_id, agent_id, created_at) VALUES(?,?,?)",
              (user["id"], agent["id"], now()))
    d.commit()
    return jsonify({"ok": True, "action": "favorited"})


@app.route("/api/comment", methods=["POST"])
def api_comment():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "留言不能为空"}), 400
    if len(content) > 500:
        return jsonify({"ok": False, "error": "留言最长 500 字"}), 400
    agent = db().execute("SELECT * FROM agents WHERE id=?", (data.get("agent_id"),)).fetchone()
    if not agent:
        return jsonify({"ok": False, "error": "Agent 不存在"}), 404
    d = db()
    cur = d.execute(
        "INSERT INTO comments(agent_id, user_id, content, created_at) VALUES(?,?,?,?)",
        (agent["id"], user["id"], content, now()),
    )
    d.commit()
    row = d.execute(
        "SELECT c.*, u.name AS user_name FROM comments c "
        "JOIN users u ON u.id = c.user_id WHERE c.id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return jsonify({"ok": True, "comment": dict(row)})


@app.route("/api/comment/delete", methods=["POST"])
def api_comment_delete():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    row = db().execute(
        "SELECT c.*, a.author_id FROM comments c "
        "JOIN agents a ON a.id = c.agent_id WHERE c.id = ?",
        (data.get("comment_id"),),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "留言不存在"}), 404
    if user["id"] != row["user_id"] and user["id"] != row["author_id"] and not user["is_admin"]:
        return jsonify({"ok": False, "error": "无权限"}), 403
    d = db()
    d.execute("DELETE FROM comments WHERE id=?", (row["id"],))
    d.commit()
    return jsonify({"ok": True})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    agent = db().execute("SELECT * FROM agents WHERE id=?", (data.get("agent_id"),)).fetchone()
    if not agent:
        return jsonify({"ok": False, "error": "Agent 不存在"}), 404
    if not DEEPSEEK_KEY:
        return jsonify({"ok": False, "error": "服务器未配置 DEEPSEEK_API_KEY"}), 500
    allowed, msg = chat_allowed(user["id"])
    if not allowed:
        return jsonify({"ok": False, "error": msg}), 429
    d = db()
    d.execute("INSERT INTO chat_log(user_id, created_at) VALUES(?,?)", (user["id"], now()))
    d.commit()
    params = json.loads(agent["params"] or "{}")
    history = data.get("history") or []
    reply = deepseek_chat(
        agent["model"], agent["system"], history,
        params.get("temperature", 0.7), params.get("top_p", 1.0),
        params.get("max_tokens", 2048),
    )
    return jsonify({"ok": True, "reply": reply})


@app.route("/api/admin/set-status", methods=["POST"])
def api_set_status():
    user = current_user()
    if not user or not user["is_admin"]:
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("pending", "approved", "rejected"):
        return jsonify({"ok": False, "error": "状态不合法"}), 400
    d = db()
    d.execute("UPDATE agents SET status=? WHERE id=?", (status, data.get("agent_id")))
    d.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/retest", methods=["POST"])
def api_retest():
    user = current_user()
    if not user or not user["is_admin"]:
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(silent=True) or {}
    row = db().execute("SELECT * FROM agents WHERE id=?", (data.get("agent_id"),)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "Agent 不存在"}), 404
    result, tested_at = auto_test(row)
    d = db()
    d.execute("UPDATE agents SET test_result=?, tested_at=? WHERE id=?",
              (result, tested_at, row["id"]))
    d.commit()
    return jsonify({"ok": True, "result": result, "tested_at": tested_at})


@app.route("/api/admin/set-theme", methods=["POST"])
def api_set_theme():
    user = current_user()
    if not user or not user["is_admin"]:
        return jsonify({"ok": False, "error": "无权限"}), 403
    data = request.get_json(silent=True) or {}
    value = (data.get("theme") or "").strip()
    d = db()
    if value:
        d.execute(
            "INSERT INTO settings(key, value) VALUES('theme_override', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (value,),
        )
    else:
        d.execute("DELETE FROM settings WHERE key='theme_override'")
    d.commit()
    return jsonify({"ok": True, "theme": value or current_theme()})


if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    app.run(host="0.0.0.0", port=8081)

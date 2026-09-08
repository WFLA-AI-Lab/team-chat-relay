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

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

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


@app.context_processor
def inject_site_links():
    """给所有模板注入教程站域名（导航条「教程站」链接用）。"""
    return {"tutorial_domain": os.environ.get("TUTORIAL_DOMAIN", "tutorial.wfla-ailab.top")}


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
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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

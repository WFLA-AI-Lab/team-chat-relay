# Organization AI Agents

面向约 10 名成员的小型团队 AI Agent 站。管理员定义一组 AI Agent（名称、图标、描述、系统提示词，绑定 DeepSeek 模型），每名成员用自己的 Open WebUI 账号登录后，直接在模型选择器（智能体库）中看到并使用这些 Agent。上游只使用 DeepSeek 官方 API，没有共享账号、没有 OAuth 代理，因此不存在「账号被封」的风险。

## 架构

```text
成员（各自 Open WebUI 账号）
        │
        ▼
Caddy（自动 HTTPS）
  ├─ /          → Open WebUI 聊天站（模型选择器里的 Agent，即自定义模型）
  ├─ /portal    → portal 门户跳转页（三张卡片串联三站）
  ├─ /arena     → arena 擂台（创建/审核/工作流，复用聊天站登录 cookie）
  └─ 教程子域名  → tutorial 教程站（渲染 Markdown 教程）
        │
        ▼
DeepSeek 官方 API（deepseek-chat / deepseek-reasoner，按量付费）
```

- 成员聊天记录按用户隔离，管理员聊天访问默认关闭。
- Agent 定义保存在数据库中，可通过后台 UI 编辑，也可通过 `config/agents.json` + 同步脚本批量管理并纳入 Git 版本控制。
- PostgreSQL 保存账号、聊天记录和 Agent 定义；Caddy 自动申请 HTTPS 证书。
- `.env`（含 DeepSeek API Key）不进入 Git。

## 前置条件

- 一台 Linux 服务器，建议 2 核 CPU、4 GB 内存、30 GB 磁盘。
- Docker Engine 和 Docker Compose v2。
- 一个域名，其 A/AAAA 记录已经指向服务器（无域名可用 SSH 隧道模式）。
- 一个 DeepSeek API Key：登录 [platform.deepseek.com](https://platform.deepseek.com) → API Keys → 创建，按量付费，没有月租。

## 1. 初始化服务器配置

```sh
./scripts/bootstrap.sh
```

编辑本地 `.env`：

- 把 `CHAT_DOMAIN` 改成真实域名（无域名部署可先留默认值）。
- 把 `WEBUI_ADMIN_EMAIL` 改成管理员邮箱。
- 把 `DEEPSEEK_API_KEY` 改成你的真实 DeepSeek API Key。
- 记下自动生成的 `WEBUI_ADMIN_PASSWORD`。

`.env` 只存在于服务器，不要提交。

## 2. 上线

### 暂时没有域名：仅通过 SSH 隧道测试

```sh
./scripts/deploy-tunnel.sh
```

在自己的电脑保持以下连接运行：

```sh
ssh -L 3000:127.0.0.1:3000 <user>@<server>
```

然后只在这台电脑打开 `http://127.0.0.1:3000`。购买域名并完成解析后，再改用正常部署。

### 有域名：HTTPS 正式上线

```sh
./scripts/deploy.sh
```

打开 `https://你的域名`，使用 `.env` 中的管理员账号登录。确认成功后，可以把 `.env` 中的 `WEBUI_ADMIN_PASSWORD` 清空；密码哈希已经保存在数据库中。

## 3. 创建成员账号

在 Open WebUI 中进入：

```text
Admin Panel → Users → Add User
```

为每名成员建立不同账号，角色保持 `User`。注册功能默认关闭。

## 4. 管理 Agent（两种方式等价）

Agent 就是 Open WebUI 里的「自定义模型」：一个名字、图标、描述 + 一段系统提示词 + 一个 DeepSeek 基座模型。建好后所有成员都能在模型选择器（智能体库）里直接看到并点击开聊。

### 方式 A：后台 UI（日常快速编辑）

```text
Admin Panel → Models → Create Model
```

填写名称、图标、描述、System Prompt，模型选择 `deepseek-chat`（V3，快）或 `deepseek-reasoner`（R1，深推理）。保存后立即生效。

### 方式 B：配置文件 + 同步脚本（推荐，可进 Git）

编辑 `config/agents.json`，然后执行：

```sh
python3 ./scripts/sync-agents.py
```

脚本用 `.env` 里的管理员账号登录本机 Open WebUI API（仅监听 127.0.0.1:8080），自动创建或更新每个 Agent。**注意**：运行后，`config/agents.json` 里同名 Agent 的 UI 修改会被覆盖——以配置文件为准。

Agent 支持 `tag` 字段（`学习` / `社团` / `其它`），同步后成为模型标签，成员可按分类筛选。

`config/agents.json` 当前预置 **7 个 Agent**：通用助手、深度思考、代码助手、招新问答助手 + 三个页面专属管家（**聊天站管家** / **擂台管家** / **教程写作**，各管一个站点）。Agent 可带 `welcome` 字段，作为该 Agent 起始对话的欢迎语。

成员侧边栏的预置对话（含欢迎语）由 `scripts/seed-starter-chats.py` 批量创建/补充：对每个非管理员账号、每个 Agent 建一条起始对话，并生成模型锁定表（预置对话模型不可切换）；新成员加入后重跑一次即可：

```sh
python3 ./scripts/seed-starter-chats.py
```

## 5. 成员使用

1. 登录自己的账号。
2. 点击输入框旁的模型选择器（智能体库），看到管理员定义的全部 Agent（带图标和描述）。
3. 点选某个 Agent，直接开聊。

成员不需要任何配置；每个 Agent 的对话独立保存，仅本人可见。

## 5.5 门户页与三站导航

站点入口是聊天站域名下的 `/portal`（门户跳转页），三张卡片分别指向三个站点：

- **聊天站** `/`：Open WebUI，用 Agent 聊天（模型选择器里的 Agent 即自定义模型）。
- **Agent 擂台** `/arena`：创建自己的 Agent、把多个 Agent 串成工作流（流水线）、试聊/收藏/留言，复用聊天站登录 cookie，免二次登录。
- **教程站** `tutorial.wfla-ailab.top`：AI 学习教程（Markdown 渲染）。

三个站点互相导航：擂台和教程站顶部都有「门户 / 聊天站 / 教程站（或擂台）」导航链接，随时跳转；聊天站没有导航条，在地址栏域名后加 `/portal` 回到门户。门户页底部提示三个站点的专属管家 Agent（聊天站管家 / 擂台管家 / 教程写作），在聊天站侧边栏即可找到。

部署：`portal` 服务由 `compose.yaml` 提供（端口 8083，仅本机监听），Caddy 用 `handle /portal*` 反代到 `portal:8083`；`TUTORIAL_DOMAIN` 环境变量决定教程站地址（擂台导航条用），`CHAT_DOMAIN` 供教程站导航条回跳聊天站。

## 6. 成员自建 Agent + 管理员审核（推荐玩法）

平台支持「成员创建 Agent → 管理员审核 → 全员可见」的流程，全部在网页 UI 完成，不需要碰服务器或 Git。

### 成员侧（网页操作）

1. 登录后进入左侧 **Workspace（工作区）→ Models**。
2. 点 **新建 / Create Model**，用表单填写：名称、图标、描述、System Prompt、选择 `deepseek-chat` 或 `deepseek-reasoner`。
3. 保存后该 Agent **仅自己可见**（默认私有）。
4. 在群里/线下告诉管理员「我建好了，叫 XX」，等待审核。

> 成员没有「设为公开」的权限，所以新建的 Agent 不会绕过审核直接出现在所有人面前。

### 管理员侧（网页操作）

1. 登录后进入 **Admin Panel（管理面板）→ Models**。
2. 找到成员创建的模型 → 打开 → 把可见性/访问范围改成 **Everyone（所有人）**。
3. 全站成员立刻能在模型选择器里看到并使用它 ✅

### 权限说明（已预配置在 compose.yaml）

- `BYPASS_MODEL_ACCESS_CONTROL=true`：主站不做模型访问控制——审核门禁由擂台应用承担（成员在擂台创建的 Agent 需管理员通过后才上架，不会直接出现在主站模型选择器里）。
- `USER_PERMISSIONS_WORKSPACE_MODELS_ACCESS=true`：允许普通成员在 Workspace 创建模型（练习用，默认私有）。

> ⚠️ 主站模型选择器里能看到哪些 Agent，由 Admin Panel → Models 的访问范围（Everyone）决定：擂台审核通过的 Agent 如需也在主站可用，管理员要在主站把它设为 Everyone。
> ⚠️ 不要开启成员对 `sharing.public_models` 的权限，否则审核门禁会失效。

## 7. 计费与余额检查

DeepSeek 按 token 计费，总花费 = 所有成员用量之和。建议：

- 在 DeepSeek 平台设置余额告警。
- 运行 `./scripts/check-balance.sh` 手动查余额（低于 10 元会警告）；装好 cron 后每天自动检查。
- 5 人以上同时生成时可能遇到限流；如经常并发，可在入口增加排队/限流。

## 8. Agent 擂台（arena 子站，可选）

`arena/` 是一个独立小应用，提供社团玩法：

- **成员创建 Agent**：详细表单（名称/分类/介绍/系统提示词/基础模型/温度/输出上限/示例问题），提交后进入待审核。
- **提交自动试跑**：提交时自动用「示例问题」跑一遍，把回答反馈给作者；管理员审核时也能看到试跑结果，并可一键重新试跑。
- **管理员审核**：网页上通过/拒绝，通过即上架擂台。
- **擂台浏览**：所有成员创建的 Agent（含未审核）都展示在擂台上，按「学习/社团/其它」分类筛选。
- **收藏 + 留言**：成员可收藏喜欢的 Agent、给作者留言建议（作者/管理员可删）。
- **预览 + 试聊**：擂台上可直接看系统提示词和自动试跑结果，并直接和任意 Agent 对话（走 DeepSeek API，与主站访问控制无关）。
- **试聊限流**：每人每分钟最多 10 条、每天最多 100 条（防滥用、保预算，可用环境变量调整）。
- **每周主题**：12 个主题按周自动轮换（顶部显示），管理员可在审核页覆盖。
- **Agent 工作流（流水线）**：作者可以把自己创建的多个 Agent 按顺序串成工作流——每一步**引用一个 Agent**（运行时用该 Agent 最新的模型/人设，改进 Agent 流水线自动跟上；Agent 被删则回退到创建时的快照），上一步的输出作为下一步的输入，每步的输入/输出都记录在案、详情页可展开查看（数据流条：原始输入 → 各步骤 → 最终输出）；支持「自定义步骤」（不选 Agent、手写指令）；作者/管理员可随时**编辑**流水线（名称、说明、步骤，含换 Agent），可「共享给全社团」；内置示例「文章生成流水线」（初步生成 → 专业评审 → 审查兜底降 AI 率）。
- **新手引导**：擂台内置使用指南页，包含成员玩法、创建流程、工作流和安全须知。

> 注：投票/排行榜功能已停用，保留「创建 Agent 练习」「收藏」「留言」「每周主题」「工作流」。

### 部署

1. 擂台挂在聊天站同域名子路径 `/arena`（复用登录 cookie），无需单独域名；`ARENA_SECRET` 建议设一个随机值。
2. 上传代码后部署：`./scripts/deploy.sh`（会自动拉取 python 镜像并启动 arena、portal 服务）。
3. 访问 `https://聊天站域名/arena`，用聊天站账号登录即可。

> 审核通过后，如需该 Agent 也在主站聊天站的模型选择器里出现，请在主站 Admin Panel → Models 里把对应模型设为 Everyone。

## 8.5 主站每日消息限额（防刷预算）

Open WebUI 没有内置配额，用「过滤器函数」实现：每人每天最多 300 条消息（数值宽松，可在后台调）。

安装（服务器上，项目目录内）：

```sh
python3 ./scripts/install-filter.py
```

调整额度：主站 **Admin Panel → Functions → 主站每日消息限额 → Valves** 改 `daily_limit`。

## 9. 备份与更新

```sh
./scripts/backup.sh
```

备份位于 `runtime/backups/`（含账号、聊天记录、Agent 定义），不会提交到 Git；自动保留最近 7 份。

安装每日定时任务（备份 03:15、余额检查 08:30）：

```sh
./scripts/setup-cron.sh
```

更新时先修改 `.env` 中固定的镜像版本，在测试环境验证后运行：

```sh
./scripts/deploy.sh
```

不要使用 `latest` 或无人值守自动升级。`docker compose down` 不删除数据库卷；不要使用 `down -v`，除非明确要永久删除全部数据。

## 安全说明

- `.env` 含 `DEEPSEEK_API_KEY`，永不提交；如泄漏，先在 DeepSeek 平台作废该 Key 再重建。
- 防火墙仅放行 TCP 22、80、443 和 UDP 443；Open WebUI 的 8080 只监听本机，不要公开。
- 组织仓库建议设为 Private，并启用受保护分支和必要的代码审查。

## 常用命令

```sh
docker compose ps
docker compose logs --tail=200 open-webui
docker compose logs --tail=200 arena
docker compose restart open-webui
docker compose down
```

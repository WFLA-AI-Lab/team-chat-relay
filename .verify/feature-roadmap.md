# AI Lab 平台还能力加什么功能 —— 汇总路线图

> 汇总自 5 个方向的并行调研（平台/AI 能力、教学闭环、社团运营、安全运维、对外生态），
> 由我逐条去重 + 亲自核实关键项后排序。**核实状态在每节末尾标注**（哪些是我实测的、哪些只是子代理声称）。
> 约束：零预算、高中生可维护、中文界面、浅色主题、不碰学术诚信文案。

---

## 0. 先补两个地基（已核实，建议排在所有新功能之前）

| # | 问题 | 状态 |
|---|---|---|
| 0-1 | **`arena.db` 根本没进备份**：`scripts/backup.sh` 只有一行 `pg_dump postgres`（第 12 行），擂台全部数据（用户/Agent/审核/流水线/白名单/学校账号映射）都在 `arena_data` 卷里，服务器坏了就全丢 | 我 grep 核实 ✔ |
| 0-2 | **白名单「管理员权限」勾选框是假的**：`allowlist.role='admin'` 存了但从没传导到 `users.is_admin`，勾了没用 | 我写测试复现 → 修好（10/10 通过）✔ |

修法：backup.sh 追加 `docker compose cp arena:/data/arena.db runtime/backups/arena-$STAMP.db`（热备用 sqlite3 `conn.backup()` 更稳）+ 备份 `.env`；0-2 已在 `arena/app.py` 修好并有测试守着。

---

## 1. 立刻做（S，每个 2 小时内，零风险、零依赖）

| 功能 | 解决什么 | 怎么做 | 验证 |
|---|---|---|---|
| **作品一键分享** | 作品上架没人知道，作者不会主动转发 | `projects.html` 卡片加按钮，`navigator.clipboard` 复制「作品名 + GitHub 链接 + 官网作品廊」；`showcase.html` 给每个 owner 分组加锚点 | 点按钮 → 粘贴能出完整文案 → 链接直达 |
| **共享流水线「复制到我的」** | 别人的流水线只能跑不能改，想改只能手抄步骤 | `POST /pipeline/<id>/copy` 深拷贝 `pipelines`+`pipeline_steps`，作者=自己、`is_shared=0`；`pipeline_detail.html` 加按钮 | 单测断言副本步骤数/引用一致、改副本不影响源 |
| **管理员数据看板** | 待审 Agent/作品/白名单申请/试聊量散在 5 个页面，社长看不到全貌 | `/admin/stats` 纯 SQL 聚合已有表（users/agents/chat_log/pipeline_runs/projects/join_requests）+ 纯 CSS 条形图 | 页面数字与 sqlite3 手查一致；空库不报错 |
| **站内公告栏** | 活动改期只能靠微信群，进来的人看不到 | 复用 `settings` 键值表（照 `theme_override` 的读写），`base.html` nav 下加横幅；教程站用 `tutorial/content/_notice.md`（`_` 前缀不进文章列表） | 写入 → 两站出现 → 清空消失 |
| **`validate.sh --health` 体检** | 现在的校验只验 compose 语法，发现不了「服务起来了但库坏了/备份三天没跑」 | 同脚本加 `--health`：容器全 Up？站点 200？库 integrity_check？备份新鲜度？待审数量？余额？ | 输出六段 PASS/FAIL 且退出码正确；停掉 arena 应报 FAIL |
| **备份补齐 + 校验和** | 见 0-1 | 见上；另加 `sha256sum` 清单 + 学期末拷 U 盘/学校电脑的检查清单（不上传公共网盘） | `sha256sum -c` 全 OK；改一个字节应 FAIL |

---

## 2. 值得做（M，1 天级，价值明确）

| 功能 | 解决什么 | 关键实现 | 依赖 |
|---|---|---|---|
| **教程站站内登录**（地基） | 教程站是独立子域，聊天站 cookie 带不过去 → 登录按钮点了回不来，进阶/维护篇永远打不开 | tutorial 加 `/login`：邮箱密码调 OpenWebUI `auths/signin`，成功后照 arena 的写法种 `Domain=.wfla-ailab.top` 的 token cookie；compose 给 tutorial 加 `OPENWEBUI_URL` | 无（**解锁后面所有教程功能**） |
| **学习进度 + 打卡** | 读完没记录，社员不知道自己走到哪，社长不知道谁在学 | tutorial 引入 sqlite（标准库）+ `progress(user_id, slug, done_at)`；文章底部「标为已学完」；首页卡片显示状态与进度条 | 需上一条；记得把 tutorial.db 加进备份 |
| **活动报名 + 签到码** | 活动靠群接龙、点名，出勤没数据 | arena 加 `activities/signups/checkins` 三表 + `/club` 页面；发布时生成 4 位签到码，现场投屏，社员输码签到（不做二维码，省依赖） | 先做 0-1 备份 |
| **招新报名公开页** | 官网弹窗只给微信号，新生要「复制→加好友」，转化不可见 | 免登录 `/arena/join`（3 字段：姓名/年级/微信）+ `join_leads` 表 + 后台线索列表；官网弹窗主按钮改指向它 | 无；涉及未成年人联系方式，招新季前跟老师口头说明用途 |
| **白名单过期 + 毕业清理** | 毕业生学号永久有效 = 残留账号安全洞（还能用平台、还能烧 API 额度） | `allowlist` 加 `expires_at`（PRAGMA 迁移）；`is_allowed` 判过期；新增 `cleanup-graduates.py`，默认 dry-run，按过期/180 天未登录**软停用**（不删数据） | 无；`school_accounts.last_login` 已在记 |
| **权限分级（副社长/审核员）** | 现在只有 `is_admin` 布尔，社长请假就没人能审 Agent | `users` 加 `role`（member/moderator/admin）；审核类路由放宽给 moderator，白名单/审计/成本页仍仅 admin | 无；顺带把 0-2 的 role 传导做完整 |
| **管理员操作审计** | 全仓库零审计，误拒/误停/改配置无从追溯 | `audit_log` 表 + `log_action()`，挂到 5 个现有管理路由与登录成败；`/admin/audit` 页面；表超 5000 行自动裁剪 | 无；**只记动作+目标+时间，绝不记对话正文/系统提示词/学校密码** |
| **GitHub 元数据服务端同步** | showcase 靠每个访客浏览器打 GitHub API（60 次/时/IP），作品一多星标就拉不到 | `scripts/sync-project-stats.py`（照 `sync-agents.py` 的 stdlib 风格）+ projects 表加 `stars/language/pushed_at/is_stale` 列 + 公开 API 输出新字段；cron 每日一次 | 无付费 |
| **作品精选/评优 + 届别档案** | 所有作品平级，认真的和随手传的分不出来；三年后不知道哪届做的 | projects 加 `is_featured/term/cohort`；管理员标精选；showcase 顶部精选横幅、按届分组 | 无 |
| **成本记账（分阶段）** | 预算很紧，但完全不知道钱花在谁身上、哪个模型上 | A（2h）：`check-balance.sh` 升级多供应商+阈值可配+记历史日志；B：`chat_log` 加 model/token/cost 列（`model_chat` 响应里本来就有 usage，现在被丢了）；C：`/admin/usage` 看板 | A 独立可做；B/C 较大 |

---

## 3. 战略性/大件（L 或多天，值得但要排期）

- **试聊/流水线流式输出（SSE 打字机）**：现在 `model_chat` 非流式、180s 阻塞，reasoner 长思考时像卡死。要处理三供应商 SSE 差异 + Caddy 别缓冲 SSE。体验提升最明显的一个 L 项。
- **教学闭环的后半段**：每篇选择题自测（`tutorial/quizzes/*.json`，改 json 即生效）→ 结业证书（纯 HTML + print CSS，不引库）→ 社长学习看板 + 周报导出。
- **每周任务栏 / 周报脚本**：`_weekly.md` 按周显示任务（`_` 前缀自动不进列表）；`weekly-digest.py` 周日生成周报文本，社长复制进群——零预算下最现实的"触达"形态（公众号要主体、邮件要 SMTP 和学校许可）。
- **对外：媒体报道页 / 作品廊学期快照 / 友校频道**：快照导出（S）特别推荐——GitHub Pages 免费永久，服务器换代作品还在。

---

## 4. 建议**不做**或谨慎（防止烂尾）

- **站内私信/聊天室**：要处理未成年人私聊的合规与滥用问题，收益不值。
- **公开排行榜/活跃榜**：容易被当绩效，引发焦虑；学习数据只给社长看聚合。
- **复活投票系统**：README 已注明投票停用，别复活（改做"精选/评优"更轻）。
- **任何付费 SaaS / 企业版**：零预算约束下不成立。

---

## 5. 依赖顺序（照这个顺序不会返工）

```
备份补齐(0-1) ──┬─→ 任何"往库里加数据"的功能（活动/招新/进度/审计）
                └─→ 恢复演练
教程站站内登录 ──→ 学习进度 ──→ 自测 / 证书 / 社长看板
权限分级 ──→ 审计日志 ──→ 成本看板（只给 admin）
作品廊：服务端同步 ──→ 精选/届别 ──→ 快照导出
```

---

## 6. 核实状态（诚实标注）

**我亲自核实的**：
- `backup.sh` 只有一行 `pg_dump postgres`（grep 第 12 行）；`arena_data` 是独立卷（compose 第 183/219 行）→ 0-1 成立。
- 「管理员权限」勾选框不生效：写测试先复现（FAIL）→ 修 → 通过（10/10），证据链完整。
- 教程站：文档在 `tutorial/content/`，8 篇里只有 `07-glossary`(进阶)、`08-maintainer`(维护) 设了分级，其余无 `level` 即公开；`current_username()` 的跨域 cookie 根因写在它自己的 docstring 里。
- `showcase.html` 确实在浏览器端直连 GitHub API（我写的那个页面）。

**来自子代理、我未逐条核实的**（用前请自己点一下）：
- 各条建议里的**行号引用**（如 `app.py:1452`）——大概率对，但文件被我改过，行号可能已漂移。
- OpenWebUI 具体端点名（`/api/v1/models`、`/api/v1/credits`）与返回字段——需在服务器上实测。
- 价格/预算数字（如「¥50/月」「R1 贵数倍」）——按官方计价页现价为准，**别写死在代码里**。
- 「投票已停用」来自 README 表述，未逐字核对代码。

---

## 7. 如果只做三件事

1. **把 arena.db 加进备份**（半小时，防灭顶）——现在丢了就真没了。
2. **教程站站内登录**（1 天，解锁整个教学闭环的地基）。
3. **活动报名 + 签到**（1 天，社团运营最高频的痛点，也是"平台有人用"的来源）。

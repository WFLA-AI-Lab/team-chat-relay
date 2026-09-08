---
title: 维护者手册：把这套站子传下去
level: 维护
---

# 维护者手册：把这套站子传下去

这份手册写给下一任维护者。社团站子每年都会换人接管，本文的目标是：**你读完这一篇，就能在没有前任指点的情况下接手运维。**

## 一、这套系统是什么

一台服务器上跑着四个站、六个容器：

| 站点 | 地址 | 容器 | 作用 |
|---|---|---|---|
| 聊天站 | chat.wfla-ailab.top | open-webui + postgres | 日常用 Agent 对话，预置七位 Agent |
| 门户页 | chat.wfla-ailab.top/portal | portal | 三张卡片串联三站，新手动线 |
| Agent 擂台 | chat.wfla-ailab.top/arena | arena | 社员建 Agent、串工作流、试聊 |
| 教程站 | tutorial.wfla-ailab.top | tutorial | 教程文章，改 markdown 即生效 |

连接关系：Caddy 统一收 443 流量并自动签 HTTPS 证书，按路径分发到各容器；所有模型推理走 DeepSeek 官方 API（服务器上不跑模型，所以 4GB 内存够用）。

## 二、日常运维三件事

### 1. 每天自动发生的（不用管）

- 凌晨 03:15 自动备份数据库（`setup-cron.sh` 装的定时任务）；
- 早上 08:30 自动检查 DeepSeek 账户余额。

### 2. 每周要看的

- `docker compose ps` 六个容器是不是全部 Up；
- 擂台新提交的 Agent 有没有待审核的（管理员登录擂台侧边栏「审核」）；
- 余额检查有没有报警。

### 3. 出问题时按这个顺序排查

1. 打开聊天站，确认是全站挂了还是个别人挂了（个人问题多是浏览器缓存，让他无痕窗口试试）；
2. `docker compose logs --tail=50 容器名` 看日志；
3. 单个容器异常就 `docker compose restart 容器名`；
4. 数据库问题先看磁盘是不是满了（`df -h`），备份文件在 `runtime/backups`；
5. 都不行，把日志整理好发到社团维护群，别一个人硬扛。

## 三、常见任务的菜谱

### 换一届管理员

聊天站：管理员界面把新届账号提权 → 验证通过后把旧账号降权。擂台：`arena.db` 的 users 表把 `is_admin` 置 1。**交接后立刻改 `.env` 里的管理员密码并 `docker compose up -d` 重启。**

### 加一篇教程

在 `tutorial/content/` 放一个 `NN-名字.md`，写好 frontmatter（title，level 写「入门」「进阶」或「维护」），刷新教程站就出现。文风参照已有文章：分节用「一、二、三」，多用表格和例子，不用 emoji，结尾给学生一句话总结。

### 加一个预置 Agent

在 `config/agents.json` 里按现有格式加一个（含 welcome 欢迎语），然后跑 `scripts/sync-agents.py` 同步进聊天站，再跑 `scripts/seed-starter-chats.py` 给全社员发预置对话。改 json 就行，不需要改代码。

### 升级 Open WebUI 版本

改 `.env` 里的镜像 tag（不要用 latest）→ `docker compose pull` → `docker compose up -d`。升级前先手动跑一次备份脚本。**每学期升级一次足够，不追新。**

### 服务器搬家

新服务器装 docker → 整个仓库目录拷过去 → `./scripts/bootstrap.sh` 生成新 .env → 填入域名、管理员邮箱和 DeepSeek Key → `./scripts/deploy.sh` → 把 `runtime/backups` 里最近的备份恢复进新 postgres。域名 DNS 的 A 记录改指新服务器 IP 即可。

## 四、底线（前任们定下的规矩）

1. **不 git commit 进主干除非前任或指导老师同意**——仓库历史要干净；
2. **.env 永远不进 git**，里面有数据库密码和 API Key；
3. **上游只走 DeepSeek 官方 API**，不接来历不明的中转；
4. 改动先在分支上改，`validate.sh` 过了再部署；
5. **每学期结束前写一份交接文档**（模式参照仓库根目录的 HANDOFF-交接文档.md：现状、改动、约束、坑），这是本站能一年年传下去的真正原因。

## 记住一句话

运维的功夫在平常：每周五分钟看一眼容器和余额，胜过期末通宵救火；把交接文档写清楚，是这个社团最值钱的传统。

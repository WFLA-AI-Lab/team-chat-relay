# 下一步在服务器上做什么（整段粘贴即可）

服务器上的仓库在 `/opt/team-chat-relay`。
**本机没有到服务器的 SSH**，下面每条命令都要你在**已经登上去的那个 SSH 窗口**里跑。
每条都写全了命令（没有需要你自己替换的占位符），照抄即可——所有命令都以
`cd /opt/team-chat-relay` 开头，不需要再敲主机名（这也是这里不写服务器 IP 的原因）。

---

## 0. 先把新代码拉下来

```bash
cd /opt/team-chat-relay && git pull && git log --oneline -1
```

拉完先别急着重建容器：本轮改动里只有 `arena` 与 `tutorial` 两个服务需要重建，
第 3 步会一起做。

---

## 1. 【P0】确认备份真的在跑

交接文档里这条一直是"待确认"（上一轮没验证过）。现在确认它：

```bash
cd /opt/team-chat-relay && ls -la runtime/backups/
```

**期望**：看到 `openwebui-*.sql`、`arena-*.db`、`env-*.env`、`MANIFEST-*.sha256`，且时间戳是最近的。

如果目录不存在或是空的，就手动跑一次：

```bash
cd /opt/team-chat-relay && bash scripts/backup.sh && ls -la runtime/backups/
```

`MANIFEST-*.sha256` 的时间戳越新越好——体检脚本（第 2 步）会用它的"年龄"判断备份有没有停。

---

## 2. 一键体检（本轮新增）

```bash
cd /opt/team-chat-relay && bash scripts/validate.sh --health; echo "退出码=$?"
```

它会依次报六段：

1. **容器**：四个服务是不是都在 running
2. **四站点**：`wfla-ailab.top` / `chat.` / `tutorial.` / `showcase.html` 的 HTTP 状态码
3. **数据库**：`pg_isready` + PostgreSQL 表数量 + arena 的 `PRAGMA integrity_check`
4. **备份新鲜度**：`MANIFEST-*.sha256` 有没有超过 `BACKUP_MAX_AGE_HOURS`（默认 48 小时）
5. **待处理队列**：有多少 agent 待审核、多少入社申请没处理
6. **API 余额**：跑 `scripts/check-balance.sh`（没有这个脚本就 SKIP）

**看结尾**：全 PASS 最好；出现 FAIL 时会打印 `N 项失败` 并以退出码 1 结束。
SKIP 是"这项没条件查"，不是失败。

> 这个脚本的**默认模式没变**（还是打印 `Compose configuration is valid.`），
> CI 里跑的就是默认模式，所以这次改动不会影响 CI。

---

## 3. 白名单收紧（本轮新增，默认干跑）

这一条补的是交接文档 §6 记录的 **P1-3 缺口**：白名单只管住 `/arena/school-login`
（路径 A），而**已经存在的账号**仍能从聊天站原生登录表单进来（路径 B）。
聊天站的登录接口不在我们代码里、拦不住，能改的是**账号的 role**——
`role=pending` 的账号登不进来。

**先干跑**（默认就是干跑，什么都不改）：

```bash
cd /opt/team-chat-relay && bash scripts/enforce-allowlist.sh
```

它会打印一份名单：**哪些账号会被降级**、哪些会被保留。看完再决定。

确认名单没问题，才真的执行：

```bash
cd /opt/team-chat-relay && bash scripts/enforce-allowlist.sh --apply
```

它会保留（绝不动）这些账号：

- 管理员（`role=admin`）与 `.env` 里的 `WEBUI_ADMIN_EMAIL`
- **白名单里**的账号（`allowlist` 表里 status=active 的）
- 已经是 `pending` 的（不用重复处理）
- 没有邮箱的账号（信息不全，宁可不动）

它**只改 role 一个字段**：不删用户、不改密码、不碰管理员。
如果想额外保下某个账号（比如指导老师）：

```bash
cd /opt/team-chat-relay && bash scripts/enforce-allowlist.sh --apply --include teacher@wfla-ailab.top
```

**想回滚**：在聊天站后台（`chat.wfla-ailab.top` → 管理员面板 → 用户 → 编辑）把该账号的
role 从 `pending` 改回 `user` 即可，或者用 `--include` 再跑一次。

> ⚠️ **跑之前想清楚**：执行后，"不在白名单里"的老账号会立刻登不进来。
> 如果社团里有一批老成员还没加白名单，**先**在 `/arena/admin/allowlist` 把他们加进去，
> **再**跑 `--apply`。

---

## 4. 学校登录开关：**不要顺手打开**

`SCHOOL_AUTH_ENABLED` 现在是 `0`，这是**有意设计的默认值**。
打开的前提是：你已经跟学校 IT 谈过、拿到了许可（技术理由见交接文档第 14 节——
那一段也是明文 HTTP，代验证会把学生密码明文经过网络）。

确认拿到许可后，命令见交接文档 **§5**（整段粘贴，最后一行必须打印 `200`；现在是 `302`）。

---

## 5. 站内公告（本轮新增，两个站两条路）

### 聊天站 / 擂台：网页里改，免 SSH

浏览器打开 <https://chat.wfla-ailab.top/arena/admin/stats>（需管理员登录）→
页面上的"站内公告"输入框 → 保存。**立即生效**，全站每个页面顶部都会出现横幅。
存的位置是 arena 的 `settings` 表里的 `notice` 键；想撤掉就把框清空再保存。

### 教程站：服务器上改一个 md 文件

```bash
cd /opt/team-chat-relay && printf '# 招新宣讲改到下周三 16:30\n' > tutorial/content/_notice.md
```

**不需要重启容器**：`tutorial` 把 `./tutorial:/app` 挂进去了，而且公告是每次请求
现读文件的，刷新页面就能看到。想撤掉就清空这个文件：

```bash
cd /opt/team-chat-relay && : > tutorial/content/_notice.md
```

（文件不存在或内容为空时，横幅自动不显示——不会出现一条空的橙条。）

---

## 6. 重建容器让新代码生效

第 0 步 `git pull` 只更新了文件；Python 服务要重启才会加载：

```bash
cd /opt/team-chat-relay && docker compose up -d --force-recreate arena tutorial
docker compose ps
```

`arena` 是新看板/公告/流水线复制，`tutorial` 是新公告文件。聊天站（open-webui）
本轮没改，不用重建。

**重建完自检**（应该都打印 200）：

```bash
for u in https://wfla-ailab.top/ https://chat.wfla-ailab.top/ https://tutorial.wfla-ailab.top/ https://wfla-ailab.top/showcase.html; do printf '%-42s ' "$u"; curl -s -o /dev/null -m 15 -w '%{http_code}\n' "$u"; done
```

然后看板应该能打开：<https://chat.wfla-ailab.top/arena/admin/stats>

---

## 本机已经验证过的东西（2026-09-11，不用你重复）

- 四个站点从**本机**访问都是 `200`，且 TLS 证书校验通过（`ssl_verify_result=0`）——
  交接文档 P1-4（GitHub Pages 证书待复测）**已确认正常**。
- 全部离线测试跑绿：11 个测试文件、约 100 项断言。
- 5 个页面的渲染布局体检通过（无横向溢出、条形图非 0 宽、公告横幅有字）。
- 2 项像素级体检通过：看板 16 条蓝条真的画出来了；作品页恰好一张卡被高亮。
  （而且**故意把老 bug 改回去，检查会立刻 FAIL** —— 说明这个检查不是摆设。）

**没验证过的**（老实说）：服务器上的一切——`git pull` 之后容器状态、
`backup.sh` 是否真的在跑、`enforce-allowlist.sh --apply` 的实际效果。
这些只有你跑完才知道，本机没有 SSH。

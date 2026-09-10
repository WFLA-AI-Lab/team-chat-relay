# 让社团平台用「学校账号 + 白名单」登录：怎么做、做了什么、你要做什么

> 结论一句话：**「账号密码和学校账号一致」不能靠同步密码实现，只能靠"代验证"**——
> 社员在你的网站里输学校账号密码，你的服务器把它转给学校系统验证一次，通过了才放行。
> 白名单（谁能进）完全由你掌握，已经做好了。
> **前提**：得先跟学校打招呼拿到许可，否则这事长得就像钓鱼站。

---

## 一、先说清一个技术事实

学校系统里存的是密码**哈希**，任何人都拿不到学生的明文密码。所以"两边密码自动一致"
只有两种真正可行的做法：

| 做法 | 是什么 | 谁能做 |
|---|---|---|
| **代验证**（本次实现） | 社员在社团平台输学校账号密码 → 服务器拿去学校系统验证一次 → 通过放行。密码用完即弃 | 你，不需要学校改任何东西 |
| **真 SSO** | 学校提供 OAuth / OIDC / CAS / LDAP，跳转到学校页面登录后带票据回来 | 必须学校信息中心配合 |
| ~~同步密码~~ | 把学校密码复制到你的库 | **不可能**（哈希不可逆），也不该做 |

---

## 二、实测到的事实（2026-09-10，从本机直连实测）

- 登录接口：`POST http://101.227.232.33:8001/Home/Login`
  表单字段 `code`（学号/工号）、`password`、`remember`
- 返回 JSON：
  - 通过 → `{"ResultType":0,"Message":"","Data":{"Url":"/"}}`
  - 密码错 → `{"ResultType":1,"Message":"Password error","Data":null}`
  - 账号不存在 → `{"ResultType":1,"Message":"Login account does not exist","Data":null}`
- **没有验证码、没有 CSRF token**（所以服务端代验证技术上成立）
- 全站未登录一律 `302 → /Home/Login`；`/api`、`/swagger` 都是 404，没有公开 API
- 顺带发现两个**值得反馈给学校**的点：
  1. 学校站点是 **http 明文**（不是 https），密码在网络上裸奔；
  2. 勾"记住密码"会把密码 **base64 后写进 cookie**（等价明文保存）。

---

## 三、三种方案，你选哪个

| 方案 | 社员体验 | 风险 | 你需要什么 |
|---|---|---|---|
| **A. 学校账号代验证 + 白名单**（已实现，默认关闭） | 一个账号走遍聊天站/擂台/教程站 | 社员把学校密码交给你的网站——必须学校知情同意 | 找信息中心/指导老师报备 |
| **B. 只做白名单，各人自己设密码** | 需要自己注册/激活一次 | 零风险，不碰学校凭据 | 无 |
| **C. 真 SSO（学校开 OAuth/CAS/LDAP）** | 最顺，跳学校页面登录 | 最低 | 学校开发排期，短期不现实 |

> ⚠️ 准确说明：本次实现的白名单是**「用学校账号登录」这条路上的准入判断**（`/school-login`）。
> 如果你暂时不接学校账号、只想「只有社员能进」，两条路：
> 1. 在聊天站后台**关掉自助注册**（OpenWebUI 设置里关 Sign Up），由你手动给社员建号——现在就能做；
> 2. 让我把同一个白名单**也套到普通登录上**（改动很小，说一声就行）。

**建议**：先按 B 加白名单把"只让社员进"这件事落地（现在就能做），
同时找学校走 A 的报备；报备通过就把开关打开（一行配置），体验直接升级到 A。

---

## 四、已经做好的东西（代码已合入，默认关闭，不影响线上现状）

| 文件 | 作用 |
|---|---|
| `arena/school_auth.py` | 学校系统校验 + 白名单增删查 + 学号→内部账号映射（自包含，可单测） |
| `arena/app.py` | 新增 `/school-login`（学校账号登录）、`/admin/allowlist`（社长后台）；登录 cookie 种到 `.wfla-ailab.top`，一套登录态贯通 chat./tutorial. |
| `arena/templates/school_login.html` | 登录页：学号 + 学校密码；被白名单拦下时变成"申请开通"页 |
| `arena/templates/admin_allowlist.html` | 后台：粘贴学号批量加、停用、删除、看待处理申请 |
| `arena/templates/login.html`、`base.html` | 加"用学校账号登录"入口；管理员导航加"白名单" |
| `compose.yaml`、`.env.example` | `SCHOOL_AUTH_ENABLED` 等 4 个配置项 |
| `tests/test_school_auth.py` | 13 项自测（含**真实学校端点**契约校验）——已全绿 |
| `tests/test_school_login_route.py` | 9 项路由联调（白名单拦截、cookie、内部账号随机密码等）——已全绿 |
| `scripts/smoke-school-auth.sh` | 部署后在服务器上跑的体检脚本 |

### 登录时到底发生了什么

```
社员输入 学号 + 学校密码
   ↓ 服务器 POST 给学校系统校验（密码用完即弃，不落库、不写日志）
不是真实账号 ❌ → "学校系统里没有这个账号"
密码不对   ❌ → "学校账号或密码错误"
通过了，但不在白名单 ❌ → "还没开通，申请已记录"（后台能看到，你点一下就能开通）
通过了，且在白名单 ✅
   ↓ 自动为该学号开一个聊天站内部账号（随机密码，只存本地库，社员看不到）
   ↓ 建立会话 + 种 .wfla-ailab.top 登录 cookie（30 天）
进入聊天站 / 擂台 / 教程站（同一套登录态）
```

---

## 五、你要做的（按顺序，共 5 步）

### 第 0 步：先问学校（决定要不要往下走）

可以直接复制这段话发给信息中心 / 社团指导老师：

> 老师您好，我是 AI 社团社长。社团的实践平台（wfla-ailab.top）想让社员用学校综合系统的
> 账号登录，这样不用记第二套密码。我们的做法是：社员在我们网站输入学校账号密码后，
> 由我们的服务器调用一次学校系统的登录接口做校验，**我们不保存任何人的学校密码**，
> 校验通过后只为他在我们平台内建一个独立账号。想请教：
> 1）这样做学校是否允许？需要走什么流程？
> 2）学校系统是否有 OAuth / CAS / 统一登录接口可以对接（这样更规范，我们优先用这个）？
> 3）另外反馈两个安全问题：学校系统目前是 http 明文传输密码；勾选"记住密码"会把密码
> base64 后存进浏览器 cookie。建议学校评估。

### 第 1 步：服务器上更新代码

```bash
cd /opt/team-chat-relay
git stash list
git checkout -- Caddyfile 2>/dev/null
git pull
docker compose up -d --force-recreate arena
docker compose logs --tail=30 arena
```

### 第 2 步：先别开开关，跑一次体检

```bash
cd /opt/team-chat-relay
bash scripts/smoke-school-auth.sh
```

要看到：第 2 步"容器能访问学校系统"是 **PASS**、第 3 步管理员接口是 **PASS**。
（第 2 步若 FAIL：说明服务器出网被限制或学校系统只允许校内网访问，这时方案 A 走不通，
退回方案 B 即可。）

### 第 3 步：先加白名单，再开开关

1. 用社长账号登录 `https://chat.wfla-ailab.top/arena`，导航条右侧出现「白名单」；
2. 把社员学号粘进"批量加入"，一行一个，支持 `2025001,张三` 这种写法；
   给副社长加时勾上"直接给管理员权限"；
3. 然后打开开关并重启：

```bash
cd /opt/team-chat-relay
sed -i 's/^SCHOOL_AUTH_ENABLED=.*/SCHOOL_AUTH_ENABLED=1/' .env
grep SCHOOL_AUTH_ENABLED .env
docker compose up -d arena
```

之后登录页就会出现「用学校账号登录」，社员用学号 + 学校密码进入。

### 第 4 步：自己先试一遍

用一个**你自己的**学号先登录一次：能进 = 通了；提示"还没开通" = 你忘了把自己加进白名单
（后台点一下"开通"即可，申请人会出现在待处理列表里）。

### 回滚（30 秒）

```bash
cd /opt/team-chat-relay
sed -i 's/^SCHOOL_AUTH_ENABLED=.*/SCHOOL_AUTH_ENABLED=0/' .env
docker compose up -d arena     # 入口立刻消失，回到原来的登录方式
```

代码层面也留了退路（改动前的存档点，本机 git tag）：

```bash
git checkout backup-before-school-auth-20260910 -- arena/app.py arena/templates/base.html \
  arena/templates/login.html compose.yaml .env.example     # 只撤改动，不动新文件
rm arena/school_auth.py arena/templates/school_login.html \
   arena/templates/admin_allowlist.html                    # 再删掉新增文件即完全复原
```

---

## 六、安全红线（改动这块代码时必须守住）

1. **绝不保存、绝不打印学生的学校密码**：`school_auth.verify()` 只把密码放进一次 HTTP 请求体。
2. **平台内部账号密码随机生成**（`secrets.token_urlsafe`），学生不需要也不应该知道；
   已有测试专门断言它 ≠ 学生输入的学校密码。
3. **必须走 HTTPS**：在明文页面上收学校密码，比学校自己的 http 还危险。平台已是 HTTPS。
4. **白名单最小化**：默认拒绝（不在名单里一律拦），停用比删除更可逆（保留记录）。
5. **开关即开关**：`SCHOOL_AUTH_ENABLED=0` 时 `/school-login` 直接 302 回普通登录，不留后门。
6. 只用**不存在的账号**做探活测试，不要拿真实学号试密码（会被学校系统记录）。

---

## 七、还没验证的部分（诚实清单）

- **聊天站内部账号自动开通**（`POST /api/v1/auths/add`）：本机没有到服务器的 SSH，
  无法在这里真连 OpenWebUI 验证；路由联调时用的是打桩的假聊天站。
  → 服务器上跑 `scripts/smoke-school-auth.sh` 第 3 步确认。
- **真实学生账号的完整登录**：我没有真实凭据，只验证了"账号不存在/密码错误/白名单拦截"
  这些分支，以及打桩下的成功分支。真实成功路径要你自己用真学号走一遍（第 4 步）。
- 学校接口若哪天加了验证码，`verify()` 会落到 `bad_reply` 分支并给社员友好提示，
  届时需要改方案（或转真 SSO）。

---

## 附：本次自测结果

```
tests/test_school_auth.py --live      13/13 通过（含真实学校端点契约校验）
tests/test_school_login_route.py       9/9  通过
渲染复核（真实模板 + 假学校/假聊天站）：
  .verify/render/school-login.html           登录入口页
  .verify/render/school-login-pending.html   通过校验但不在白名单 → 申请页
  .verify/render/admin-allowlist.html        社长白名单后台
  .verify/render/login.html                  普通登录页（含学校账号入口）
截图：school-login.png / school-login-pending.png / admin-allowlist.png / login-with-school-entry.png
```

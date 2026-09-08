---
title: DSH 是什么，怎么部署
---

# DSH 是什么，怎么部署

> DSH = **DeepSeek Harness**，由 DeepSeek AI 开发的开源 agent harness（智能体框架）。你正在读的这篇教程所在的项目，就是在 DSH 环境里完成的。

## 一、DSH 是什么

简单说，DSH 是一个「**让 AI 自己干活**」的框架：

- 普通聊天站：你打字，AI 回话。
- DSH：你给一个目标（比如「写一个网站」），DSH 里的 agent 会**自己写代码、跑命令、看结果、改 bug**，直到任务完成。

它有几个特点：

- **一切皆插件**：模型、工具、能力都是插件，按需加载。
- **由 Cordis 驱动**：插件化、可组合的架构。
- **开源**（MIT 协议），GitHub：`deepseek-ai/deepseek-harness`。
- 目前是**开发者预览**阶段，迭代很快，接口可能变化。

## 二、你需要准备什么

| 东西 | 说明 |
|---|---|
| Node.js（≥ 某个版本） | 运行 DSH 的运行时 |
| DeepSeek API Key | agent 干活要调 DeepSeek 模型（按量付费） |
| 一台能联网的电脑 | 开发机或服务器都可以 |

## 三、最快部署：一条命令

先装好 [Node.js](https://nodejs.org)，然后：

```sh
npx @deepseek-ai/dsh web
```

启动后浏览器打开：

```text
http://127.0.0.1:3080
```

第一次使用会要求配置 DeepSeek API Key（去 platform.deepseek.com 创建，见「DeepSeek 基础与预算」教程）。

## 四、从源码部署（想改代码/学习时）

```sh
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build
pnpm dsh web
```

同样打开 `http://127.0.0.1:3080`。

## 五、部署到服务器（让成员也能用）

如果想让别人通过公网访问，需要一台服务器（如本社团的 VPS）加一个域名，例如：

```text
https://dsh.wfla-ailab.top  →  你的服务器
```

基本流程（和本社团聊天站的部署方式类似）：

1. 买一台 Linux 服务器，装好 Node.js。
2. 在服务器上运行 DSH（`npx @deepseek-ai/dsh web`），先确认本机 3080 能打开。
3. 配置反向代理（如 Caddy / Nginx）把域名转发到 3080，自动申请 HTTPS。
4. 防火墙只放行 22、80、443 端口，**不要把 3080 直接暴露到公网**。
5. 用 SSH 隧道或跳板机管理服务器，API Key 只放在服务器环境变量里。

## 六、注意事项

- **按量付费**：agent 干活会消耗 token，跑大任务前先估算，设好余额告警。
- **沙箱意识**：让 agent 跑代码时，注意它操作的是你的文件系统——建议在隔离目录/容器里跑。
- **别泄露 Key**：API Key 放进代码/网页前端 = 直接泄露。
- **它是预览版**：升级前看 release 说明，别盲目 `latest`。

## 动手练习

1. 在自己的电脑装 Node.js，跑通 `npx @deepseek-ai/dsh web`。
2. 给 DSH 一个真实小任务（比如「用 Python 写一个猜数字游戏」），观察它怎么拆解和干活。
3. 读 DSH 的 GitHub README 和插件文档，看它支持哪些插件。

# 🏠 HNHost 自动续期工作流

公开运行 GitHub Actions 工作流，核心运行脚本存放于统一私有仓库 `my-private-scripts/hnhost` 中。
基于 sing-box Hysteria2 代理绕过限制，自动完成 HNHost 多账号登录、每日签到、VPS 状态监控与到期自动续期，支持 Telegram 详细消息推送。

## 配置说明

需要在本仓库的 **Settings -> Secrets and variables -> Actions** 中配置以下 Secrets：

| Secret 名称 | 说明 | 是否必填 |
| :--- | :--- | :--- |
| `CORE_SCRIPT_TOKEN` 或 `REPO_TOKEN` | 具备读取私有仓库 `my-private-scripts` 权限的 GitHub Personal Access Token (PAT) | 必填 |
| `DISCORD_TOKENS` | Discord 授权 Token 列表（JSON 数组格式，例如 `["token1", "token2"]`） | 必填 |
| `TG_BOT_TOKEN` | Telegram Bot Token，用于发送通知 | 选填 |
| `TG_CHAT_ID` | Telegram 接收通知的 Chat ID | 选填 |

## 手动触发测试

1. 打开本仓库的 **Actions** 页面。
2. 选择 **HNHost 自动续期** 工作流。
3. 点击 **Run workflow** 手动触发测试。
4. 之后每天 20:00 (UTC) 自动定时执行。

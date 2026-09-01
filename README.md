# 🏠 HNHost 自动续期

基于 GitHub Actions + sing-box Hysteria2 代理，自动续期 HNHost 免费 VPS。

## ✨ 功能

- ✅ Hysteria2 代理绕过 IP 封锁
- ✅ PHPSESSID Cookie 登录（无需 Discord OAuth）
- ✅ 自动访问 create.php / renew.php 续期
- ✅ TG 消息推送
- ✅ 自动清理旧工作流日志

## 🚀 部署

### 1. 配置 GitHub Secrets


### 2. 获取 Cookie

1. 浏览器打开 https://client.hnhost.net → Discord 登录
2. F12 → Application → Cookies → `https://client.hnhost.net`
3. 复制 `PHPSESSID` 和 `cf_clearance` 的值
4. 格式: `PHPSESSID=xxx; cf_clearance=xxx`

⚠️ Cookie 有效期约 1 天，过期后 TG 会通知你重新获取。

### 3. 触发测试

Actions → HNHost 自动续期 → Run workflow

## 📂 文件结构

```
├── .github/workflows/hnhost.yml   # GitHub Actions
├── hnhost_renew.py                 # 主脚本
├── requirements.txt                # Python 依赖
└── README.md
```

#!/usr/bin/env python3
"""
HNHost 自动续期（多账号版）
Discord Token → OAuth Code → 登录 → 检查服务器状态 + 续期 + 每日领取
"""

import os, sys, re, json, requests, urllib3
from urllib.parse import urljoin
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DISCORD_TOKENS = os.environ.get("DISCORD_TOKENS", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "1080")
BASE_URL = "https://client.hnhost.net"
DISCORD_CLIENT_ID = "977981235618021377"
DISCORD_REDIRECT_URI = "https://client.hnhost.net/backend/pdo/discord.php"
DISCORD_SCOPES = "identify email guilds guilds.join"
PROXY = f"socks5h://127.0.0.1:{PROXY_PORT}"


def parse_tokens():
    tokens = []
    if DISCORD_TOKENS:
        for item in json.loads(DISCORD_TOKENS):
            if isinstance(item, str):
                tokens.append({"token": item, "name": f"账号{len(tokens)+1}"})
            elif isinstance(item, dict):
                tokens.append({"token": item["token"], "name": item.get("name", f"账号{len(tokens)+1}")})
    if not tokens:
        print("❌ 未配置 DISCORD_TOKENS"); sys.exit(1)
    return tokens


def send_tg(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=30)
        print(f"  [TG] 已推送")
    except: pass


def get_oauth_code(token: str) -> str | None:
    """Discord Token → OAuth Code"""
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope={DISCORD_SCOPES.replace(' ', '+')}"
    resp = requests.post(url, headers={"Authorization": token, "Content-Type": "application/json"},
        json={"authorize": True}, timeout=15, proxies={"http": PROXY, "https": PROXY})
    data = resp.json()
    location = data.get("location", "")
    if location:
        match = re.search(r'code=([^&]+)', location)
        if match: return match.group(1)
    print(f"  [!] OAuth Code 失败: {data}")
    return None


def login_hnhost(token: str) -> requests.Session:
    """Discord OAuth 登录 HNHost"""
    code = get_oauth_code(token)
    if not code:
        return None
    
    s = requests.Session()
    s.verify = False
    s.proxies = {"http": PROXY, "https": PROXY}
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    
    resp = s.get(f"{BASE_URL}/backend/pdo/discord.php?code={code}", timeout=20, allow_redirects=True)
    if "PHPSESSID" not in s.cookies:
        print("  ❌ 登录失败")
        return None
    return s


def get_server_info(s: requests.Session, server_id: str) -> dict:
    """获取服务器信息"""
    try:
        resp = s.get(f"{BASE_URL}/middleware/localApi/homeInfoApi.php?fx=freeServerInfo&userId={server_id}", timeout=15)
        data = resp.json()
        return data.get("response", {})
    except:
        return {}


def get_user_info(s: requests.Session, user_id: str) -> dict:
    """获取用户信息"""
    try:
        resp = s.get(f"{BASE_URL}/middleware/localApi/homeInfoApi.php?fx=userInfo&userId={user_id}", timeout=15)
        data = resp.json()
        return data.get("response", {})
    except:
        return {}


def get_server_id(page_text: str) -> tuple[str | None, str | None]:
    """从页面提取服务器 ID 和 userId"""
    # userId 在 JavaScript 中: userId=6a2c6addacbdb
    user_match = re.search(r'userId=["\']?([a-f0-9]+)', page_text)
    # serverId 在 renew 链接中: id=6a33722bcd119
    server_match = re.search(r'/index\.php\?server=renew&id=([a-f0-9]+)', page_text)
    return server_match.group(1) if server_match else None, user_match.group(1) if user_match else None


def check_and_renew(account: dict) -> bool:
    name, token = account["name"], account["token"]
    print(f"\n{'='*50}\n📍 {name}\n{'='*50}")

    # 1. 登录
    print("[1] 登录 HNHost...")
    s = login_hnhost(token)
    if not s:
        send_tg(f"📍 {name}\n❌ 登录失败"); return False
    print("  登录成功 ✅")

    # 2. 获取首页，找服务器 ID 和 userId
    print("[2] 获取服务器信息...")
    resp = s.get(BASE_URL, timeout=20)
    server_id, user_id = get_server_id(resp.text)
    
    if not server_id:
        print("  ⚠️ 无服务器")
        send_tg(f"📍 {name}\n⚠️ 无服务器")
        return True

    print(f"  Server ID: {server_id}, User ID: {user_id}")

    # 3. 获取服务器状态
    info = get_server_info(s, user_id)
    if not info:
        print("  ❌ 获取服务器信息失败")
        send_tg(f"📍 {name}\n❌ 获取服务器信息失败"); return False
    
    state = info.get("state", "Unknown")
    cpu = info.get("cpu", "?")
    ram = info.get("ram", "?")
    disk = info.get("disk", "?")
    
    print(f"  状态: {state}")
    print(f"  CPU: {cpu}%, RAM: {ram}MB, Disk: {disk}MB")

    # 4. 检查是否需要续期
    needs_renew = any(k in state for k in ["已过期", "expired", "停止", "到期", "过期"])
    
    results = []
    if needs_renew:
        print("  ⚠️ 需要续期！")
        # 访问续期页面
        renew_url = f"{BASE_URL}/index.php?server=renew&id={server_id}"
        resp2 = s.get(renew_url, timeout=20)
        if resp2.status_code == 200:
            print("  ✅ 已访问续期页面")
            results.append("✅ 服务器续期已执行")
        else:
            results.append("❌ 续期页面访问失败")
    else:
        print("  ✅ 服务器正常，无需续期")
        results.append("✅ 服务器正常（限額可用）")

    # 5. 每日领取
    print("[3] 检查每日奖励...")
    claim_btn = re.search(r'領取每日登錄獎勵', resp.text)
    if claim_btn:
        # 点击领取
        claim_url = f"{BASE_URL}/index.php?server=renew&id={server_id}"
        resp3 = s.get(claim_url, timeout=20)
        if "已領取每日獎勵" in resp3.text:
            print("  ✅ 每日奖励已领取")
            results.append("✅ 每日奖励已领取")
        else:
            results.append("⚠️ 领取状态未知")
    else:
        print("  📅 每日奖励已领取")
        results.append("📅 每日奖励已领取")

    # 6. TG 通知
    msg = f"📍 {name}\n" + "\n".join(results)
    print(f"\n{msg}")
    send_tg(msg)
    return True


def main():
    print("=" * 50)
    print("🏠 HNHost 自动续期（多账号）")
    print("=" * 50)
    accounts = parse_tokens()
    print(f"共 {len(accounts)} 个账号")

    results = []
    for account in accounts:
        try:
            success = check_and_renew(account)
        except Exception as e:
            print(f"  [!] 异常: {e}")
            send_tg(f"📍 {account['name']}\n❌ 异常: {e}")
            success = False
        results.append((account["name"], success))

    print(f"\n{'='*50}")
    print("📊 汇总")
    for name, ok in results:
        print(f"  {name}: {'✅' if ok else '❌'}")
    print(f"总计: {sum(1 for _, ok in results if ok)}/{len(results)} 成功")


if __name__ == "__main__":
    main()

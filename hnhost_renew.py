#!/usr/bin/env python3
"""
HNHost 自动续期（多账号版）
Discord Token → OAuth Code → 登录 → 检查服务器状态 + 到期时间 + 续期 + 每日领取
"""

import os, sys, re, json, requests, urllib3
from datetime import datetime, timedelta
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


def get_server_info(s: requests.Session, user_id: str) -> dict:
    """获取服务器信息"""
    try:
        resp = s.get(f"{BASE_URL}/middleware/localApi/homeInfoApi.php?fx=freeServerInfo&userId={user_id}", timeout=15)
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


def extract_expire_date(page_text: str) -> str | None:
    """从续期页面提取到期日（格式: YYYY/MM/DD）"""
    # 找表格行中包含到期日的位置
    idx = page_text.find("到期日")
    if idx < 0:
        return None
    after = page_text[idx:idx+3000]
    # 找日期格式
    dates = re.findall(r'(\d{4}/\d{2}/\d{2})', after)
    if dates:
        return dates[0]
    return None


def get_server_id_and_expire(s: requests.Session, user_id: str) -> tuple[str | None, str | None]:
    """从首页获取服务器 ID，从续期页面获取到期日"""
    # 获取首页找服务器 ID
    resp = s.get(BASE_URL, timeout=20)
    
    # userId 在 JavaScript 中
    user_match = re.search(r'userId=["\']?([a-f0-9]+)', resp.text)
    uid = user_match.group(1) if user_match else user_id
    
    # serverId 在 renew 链接中
    server_match = re.search(r'/index\.php\?server=renew&id=([a-f0-9]+)', resp.text)
    server_id = server_match.group(1) if server_match else None
    
    if not server_id:
        return None, None
    
    # 访问续期页面提取到期日
    renew_url = f"{BASE_URL}/index.php?server=renew&id={server_id}"
    resp2 = s.get(renew_url, timeout=20)
    expire_date = extract_expire_date(resp2.text)
    
    return server_id, expire_date


def calculate_days_left(expire_str: str) -> int:
    """计算剩余天数"""
    try:
        expire = datetime.strptime(expire_str, "%Y/%m/%d")
        today = datetime.now()
        return (expire - today).days
    except:
        return -1


def check_and_renew(account: dict) -> dict:
    name, token = account["name"], account["token"]
    print(f"\n{'='*50}\n📍 {name}\n{'='*50}")

    # 1. 登录
    print("[1] 登录 HNHost...")
    s = login_hnhost(token)
    if not s:
        send_tg(f"📍 {name}\n❌ 登录失败")
        return {"name": name, "success": False, "expire": None, "days_left": None, "server_id": None}
    print("  登录成功 ✅")

    # 2. 获取服务器信息和到期日
    print("[2] 获取服务器信息...")
    server_id, expire_date = get_server_id_and_expire(s, "6a2c6addacbdb")
    
    if not server_id:
        print("  ⚠️ 无服务器")
        send_tg(f"📍 {name}\n⚠️ 无服务器")
        return {"name": name, "success": True, "expire": None, "days_left": None, "server_id": None}

    print(f"  Server ID: {server_id}")
    print(f"  到期日: {expire_date or '未知'}")

    # 3. 计算剩余天数
    days_left = calculate_days_left(expire_date) if expire_date else -1
    print(f"  剩余天数: {days_left} 天")

    # 4. 获取服务器状态
    info = get_server_info(s, "6a2c6addacbdb")
    raw_state = info.get("state", "Unknown") if info else "Unknown"
    state = re.sub(r'<[^>]+>', '', raw_state).strip() if raw_state else "Unknown"
    cpu = info.get("cpu", "?") if info else "?"
    ram = info.get("ram", "?") if info else "?"
    disk = info.get("disk", "?") if info else "?"
    print(f"  状态: {state}, CPU: {cpu}%, RAM: {ram}MB, Disk: {disk}MB")

    # 5. 检查是否需要续期（提前3天自动续期，每次续期加1个月）
    results = []
    auto_renew = False
    
    if days_left <= 0:
        print(f"  🚨 服务器已到期！立即续期...")
        auto_renew = True
    elif days_left <= 3:
        print(f"  ⚠️ 服务器即将到期（{days_left} 天后），执行续期...")
        auto_renew = True
    
    if auto_renew:
        # 访问续期页面
        renew_url = f"{BASE_URL}/index.php?server=renew&id={server_id}"
        resp2 = s.get(renew_url, timeout=20)
        if resp2.status_code == 200:
            print("  ✅ 续期页面已访问（触发续期）")
            results.append("✅ 已触发自动续期")
        else:
            results.append("❌ 续期页面访问失败")
    else:
        results.append(f"✅ 服务器正常（剩余 {days_left} 天）")

    # 6. 每日领取
    print("[3] 检查每日奖励...")
    claim_found = re.search(r'領取每日登錄獎勵', s.text)
    if claim_found:
        renew_url = f"{BASE_URL}/index.php?server=renew&id={server_id}"
        resp3 = s.get(renew_url, timeout=20)
        if "已領取每日獎勵" in resp3.text:
            print("  ✅ 每日奖励已领取")
            results.append("✅ 每日奖励已领取")
        else:
            results.append("⚠️ 领取状态未知")
    else:
        print("  📅 每日奖励已领取")
        results.append("📅 每日奖励已领取")

    # 7. TG 通知（含到期时间）
    expire_emoji = "🟢" if (days_left is None or days_left > 3) else "🟡" if days_left > 0 else "🔴"
    msg = (
        f"📍 *{name}*\n"
        f"⏰ *到期时间*: {expire_date or '未知'}\n"
        f"📅 *剩余天数*: {days_left if days_left >= 0 else '已过期'} 天 {expire_emoji}\n"
        f"🖥 *状态*: {state}\n"
        f"💾 *配置*: {cpu}% CPU / {ram}MB RAM / {disk}MB Disk\n"
        f"{'─' * 25}\n"
        + "\n".join(results)
    )
    send_tg(msg)
    
    return {
        "name": name,
        "success": True,
        "expire": expire_date,
        "days_left": days_left,
        "server_id": server_id,
        "state": state,
        "msg": msg
    }


def main():
    print("=" * 50)
    print("🏠 HNHost 自动续期（多账号）")
    print("=" * 50)
    accounts = parse_tokens()
    print(f"共 {len(accounts)} 个账号")

    all_results = []
    for account in accounts:
        try:
            result = check_and_renew(account)
        except Exception as e:
            print(f"  [!] 异常: {e}")
            send_tg(f"📍 {account['name']}\n❌ 异常: {e}")
            result = {"name": account["name"], "success": False}
        all_results.append(result)

    # 汇总
    print(f"\n{'='*50}")
    print("📊 汇总")
    for r in all_results:
        name = r.get("name", "?")
        expire = r.get("expire")
        days = r.get("days_left")
        ok = r.get("success", False)
        days_str = f"{days}天" if days is not None else "未知"
        emoji = "✅" if ok else "❌"
        print(f"  {emoji} {name}: 到期 {expire or '无'} ({days_str})")
    
    success_count = sum(1 for r in all_results if r.get("success"))
    print(f"总计: {success_count}/{len(all_results)} 成功")


if __name__ == "__main__":
    main()

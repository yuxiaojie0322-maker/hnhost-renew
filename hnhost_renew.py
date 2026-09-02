#!/usr/bin/env python3
"""
HNHost 自动续期（多账号版）+ 每日签到检查
Discord Token → OAuth Code → 登录 → 签到检查 + 检查服务器状态 + 到期时间 + 续期
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


def auto_checkin(s: requests.Session, user_id: str) -> tuple[bool, str]:
    """
    自动签到
    返回: (是否成功, 状态描述)
    """
    try:
        # 先检查当前状态
        resp = s.get(BASE_URL, timeout=20)
        home_html = resp.text

        # 检查是否已签到
        if "已領取每日獎勵" in home_html:
            print("  ✅ 今日已签到")
            return True, "今日已签到"
        elif "領取每日登錄獎勵" in home_html:
            print("  ⚠️ 今日未签到，尝试自动签到...")
            # 触发签到
            checkin_url = f"{BASE_URL}/?generalEvent=dailyReward"
            resp2 = s.get(checkin_url, timeout=20, allow_redirects=True)

            if "已領取每日獎勵" in resp2.text:
                print("  ✅ 签到成功！")
                return True, "签到成功 (+10 Coins)"
            else:
                print("  ❌ 签到失败")
                return False, "签到失败"
        else:
            print("  ? 签到状态未知")
            return None, "状态未知"
    except Exception as e:
        print(f"  [!] 签到失败: {e}")
        return False, f"异常: {e}"


def get_user_info(s: requests.Session, user_id: str) -> dict:
    """获取用户信息（含余额）"""
    try:
        resp = s.get(f"{BASE_URL}/middleware/localApi/homeInfoApi.php?fx=userInfo&userId={user_id}", timeout=15)
        data = resp.json()
        return data.get("response", {})
    except:
        return {}


def check_checkin_status(s: requests.Session) -> tuple[bool, str]:
    """
    检查签到状态
    返回: (是否已签到, 首页HTML)
    """
    try:
        resp = s.get(BASE_URL, timeout=20)
        home_html = resp.text
        
        # 检查是否已签到
        if "已領取每日獎勵" in home_html:
            return True, home_html
        elif "領取每日登錄獎勵" in home_html:
            return False, home_html
        else:
            return None, home_html  # 未知状态
    except Exception as e:
        print(f"  [!] 签到检查失败: {e}")
        return None, ""


def get_server_id(s: requests.Session) -> str | None:
    """从首页获取服务器 ID"""
    try:
        resp = s.get(BASE_URL, timeout=20)
        match = re.search(r'/index\.php\?server=renew&id=([a-f0-9]+)', resp.text)
        return match.group(1) if match else None
    except:
        return None


def extract_expire_date(page_text: str) -> str | None:
    """从续期页面提取到期日（格式: YYYY/MM/DD）"""
    idx = page_text.find("到期日")
    if idx < 0:
        return None
    after = page_text[idx:idx+3000]
    dates = re.findall(r'(\d{4}/\d{2}/\d{2})', after)
    if dates:
        return dates[0]
    return None


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
        return {"name": name, "success": False, "expire": None, "days_left": None, "balance": None, "checkin": None}
    print("  登录成功 ✅")

    # 2. 获取用户 ID
    print("[2] 获取用户信息...")
    resp = s.get(BASE_URL, timeout=20)
    user_match = re.search(r'[?&]userId=([a-f0-9]+)', resp.text)
    user_id = user_match.group(1) if user_match else None
    
    if not user_id:
        print("  ❌ 无法获取 userId")
        send_tg(f"📍 {name}\n❌ 无法获取用户 ID")
        return {"name": name, "success": False, "expire": None, "days_left": None, "balance": None, "checkin": None}
    print(f"  User ID: {user_id}")

    # 3. 检查签到状态并自动签到
    print("[3] 检查签到状态...")
    checkin_success, checkin_result = auto_checkin(s, user_id)

    if checkin_success is True:
        print("  ✅ 今日已签到或签到成功")
    elif checkin_success is False:
        print("  ⚠️ 签到失败")
    else:
        print("  ? 签到状态未知")

    # 4. 获取余额
    print("[4] 获取余额...")
    user_info = get_user_info(s, user_id)
    balance = user_info.get("hncoin", "?")
    nickname = user_info.get("nickname", name)
    print(f"  💰 余额: {balance} HN Coins")

    # 5. 获取服务器信息
    print("[5] 获取服务器信息...")
    server_id = get_server_id(s)
    
    if not server_id:
        print("  ⚠️ 无服务器")
        send_tg(f"📍 {name}\n⚠️ 无服务器\n💰 余额: {balance} HN Coins\n📅 签到: {checkin_result}")
        return {"name": name, "success": True, "expire": None, "days_left": None, "balance": balance, "checkin": checkin_result}

    print(f"  Server ID: {server_id}")

    # 6. 获取到期日（只读方式，绝不无条件访问续期页，避免每天扣金币）
    #    HNHost 访问 /index.php?server=renew 可能触发续期扣费，
    #    因此到期日改为从 freeServerInfo API / 首页 HTML 提取，
    #    只有到期当天（days_left <= 0）才访问续期页。
    renew_url = f"{BASE_URL}/index.php?server=renew&id={server_id}"  # 仅在到期时使用
    expire_date = None
    info = {}
    # 6a. 从服务器信息 API 提取到期字段
    try:
        resp_info = s.get(f"{BASE_URL}/middleware/localApi/homeInfoApi.php?fx=freeServerInfo&userId={user_id}", timeout=15)
        info = resp_info.json().get("response", {})
        if isinstance(info, dict):
            for key in ("expire", "expire_time", "expireTime", "endTime", "dueTime", "deadline", "end_date", "endDate"):
                val = info.get(key)
                if val:
                    m = re.search(r'(\d{4}/\d{2}/\d{2})', str(val))
                    if m:
                        expire_date = m.group(1)
                        print(f"  [DEBUG] 到期日来自 API.{key} = {val}")
                        break
    except Exception as e:
        print(f"  [!] 服务器信息 API 失败: {e}")
    # 6b. 若 API 没有，从首页 HTML 提取（首页不含续期动作）
    if not expire_date:
        try:
            resp_home = s.get(BASE_URL, timeout=20)
            expire_date = extract_expire_date(resp_home.text)
            if expire_date:
                print("  [DEBUG] 到期日来自首页 HTML")
            else:
                idx = resp_home.text.find("到期")
                if idx >= 0:
                    print(f"  [DEBUG] 首页'到期'附近: {resp_home.text[idx:idx+200]}")
        except Exception as e:
            print(f"  [!] 首页到期日提取失败: {e}")
    print(f"  到期日: {expire_date or '未知'}（只读获取，未访问续期页）")

    # 7. 计算剩余天数
    days_left = calculate_days_left(expire_date) if expire_date else -1
    print(f"  剩余天数: {days_left} 天")

    # 8. 获取服务器状态（复用第 6 步已获取的 info，避免重复请求）
    try:
        raw_state = info.get("state", "Unknown") if info else "Unknown"
        state = re.sub(r'狀態[：:]\s*', '', raw_state).strip() if raw_state else "Unknown"
        state = re.sub(r'<[^>]+>', '', state).strip()
        cpu = info.get("cpu", "?") if info else "?"
        ram = info.get("ram", "?") if info else "?"
        disk = info.get("disk", "?") if info else "?"
        print(f"  状态: {state}, CPU: {cpu}%, RAM: {ram}MB, Disk: {disk}MB")
    except:
        state, cpu, ram, disk = "未知", "?", "?", "?"

    # 9. 检查是否需要续期（只在服务器到期当天或已过期时才续期，避免浪费金币）
    results = []
    auto_renew = False
    
    if expire_date is None:
        print("  ⚠️ 无法获取到期日，本次不续期（避免误操作）")
        results.append("⚠️ 到期日未知，未续期")
    elif days_left <= 0:
        print(f"  🚨 服务器已到期（{expire_date}）！立即续期...")
        auto_renew = True
        results.append(f"🚨 已到期，执行续期")
    else:
        print(f"  ✅ 服务器正常（剩余 {days_left} 天），无需续期")
        results.append(f"✅ 服务器正常（剩余 {days_left} 天）")
    
    if auto_renew:
        print("  [9] 触发续期...")
        # 到期当天才访问续期页面触发自动续期
        resp_renew2 = s.get(renew_url, timeout=20)
        if resp_renew2.status_code == 200:
            print("  ✅ 续期页面已访问")
            results.append("✅ 已触发续期")
        else:
            results.append("❌ 续期失败")

    # 10. 准备 TG 通知
    expire_emoji = "🟢" if (days_left is None or days_left > 3) else "🟡" if days_left > 0 else "🔴"
    checkin_emoji = "✅" if checkin_success is True else "⚠️" if checkin_success is False else "？"
    
    msg = (
        f"📍 *{nickname}* ({name})\n"
        f"💰 *余额*: {balance} HN Coins\n"
        f"📅 *签到*: {checkin_emoji} {checkin_result}\n"
        f"⏰ *到期时间*: {expire_date or '未知'}\n"
        f"📆 *剩余天数*: {days_left if days_left >= 0 else '已过期'} 天 {expire_emoji}\n"
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
        "balance": balance,
        "checkin": checkin_result
    }


def main():
    print("=" * 50)
    print("🏠 HNHost 自动续期 + 签到检查")
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
            result = {"name": account["name"], "success": False, "expire": None, "days_left": None, "balance": None, "checkin": None}
        all_results.append(result)

    # 汇总
    print(f"\n{'='*50}")
    print("📊 汇总")
    total_balance = 0
    success_count = 0
    fail_count = 0
    for r in all_results:
        name = r.get("name", "?")
        balance = r.get("balance")
        checkin = r.get("checkin", "?")
        expire = r.get("expire")
        days = r.get("days_left")
        ok = r.get("success", False)
        
        if ok:
            success_count += 1
            balance_str = f"{balance} Coins" if balance and balance != "?" else "未知"
            # 尝试将余额转换为数字并累加
            try:
                if balance and balance != "?":
                    total_balance += int(balance)
            except:
                pass
        else:
            fail_count += 1
            balance_str = "登录失败"
        
        days_str = f"{days}天" if days is not None else "未知"
        checkin_str = checkin[:10] if isinstance(checkin, str) and checkin else "未知"
        emoji = "✅" if ok else "❌"
        print(f"  {emoji} {name}: 余额={balance_str}, 签到={checkin_str}, 到期={expire or '无'} ({days_str})")
    
    print(f"{'─' * 50}")
    print(f"💰 总余额: {total_balance} HN Coins")
    print(f"总计: {success_count} 成功 / {fail_count} 失败")


if __name__ == "__main__":
    main()

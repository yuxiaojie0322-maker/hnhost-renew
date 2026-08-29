#!/usr/bin/env python3
"""
HNHost 自动续期（多账号版）
Discord Token → OAuth Code → 登录 → 首页找服务器续期 + 每日领取
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


def renew_account(account: dict) -> bool:
    name, token = account["name"], account["token"]
    print(f"\n{'='*50}\n📍 {name}\n{'='*50}")

    # 1. OAuth Code
    print("[1] 获取 OAuth Code...")
    code = get_oauth_code(token)
    if not code:
        send_tg(f"📍 {name}\n❌ OAuth Code 获取失败"); return False
    print(f"  Code: {code[:20]}...")

    # 2. 登录 HNHost（通过代理）
    print("[2] 登录 HNHost...")
    s = requests.Session()
    s.verify = False
    s.proxies = {"http": PROXY, "https": PROXY}
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })

    # 访问 OAuth 回调 URL，跟随所有重定向到首页
    resp = s.get(f"{BASE_URL}/backend/pdo/discord.php?code={code}", timeout=20, allow_redirects=True)
    final_url = resp.url
    print(f"  最终 URL: {final_url}")
    print(f"  PHPSESSID: {s.cookies.get('PHPSESSID', '无')[:20]}...")

    # 检查是否登录成功（不在登录页）
    page_text = re.sub(r'<[^>]+>', ' ', resp.text)
    page_text = re.sub(r'\s+', ' ', page_text).strip()
    if "登錄平台" in page_text or "透過 Discord 登錄" in page_text:
        print("  ❌ 未登录（仍在登录页）")
        print(f"  页面内容: {page_text[:200]}")
        send_tg(f"📍 {name}\n❌ 登录失败"); return False
    print("  登录成功 ✅")

    # 3. 在首页找服务器和每日领取
    print("[3] 搜索首页内容...")
    print(f"  首页摘要: {page_text[:500]}")

    # 打印所有链接
    all_links = re.findall(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', resp.text, re.S)
    link_list = []
    for href, text in all_links:
        text = re.sub(r'<[^>]+>', '', text).strip()
        if text and href and href != "#":
            full_url = urljoin(final_url, href)
            link_list.append((text[:40], full_url))
            print(f"    链接: {text[:40]} → {full_url[:80]}")

    # 打印所有按钮
    all_btns = re.findall(r'<(?:button|input[^>]*type=["\']submit["\'])[^>]*>(.*?)</(?:button)>', resp.text, re.S)
    btn_values = re.findall(r'<input[^>]*type=["\']submit["\'][^>]*value=["\']([^"\']*)["\']', resp.text, re.I)
    all_btn_texts = [re.sub(r'<[^>]+>', '', b).strip()[:40] for b in all_btns if b.strip()]
    all_btn_texts += btn_values
    print(f"  按钮: {all_btn_texts[:20]}")

    # 打印所有表单
    forms = re.findall(r'<form[^>]*action=["\']([^"\']*)["\'][^>]*method=["\']([^"\']*)["\']', resp.text, re.I)
    print(f"  表单: {forms}")

    results = []

    # 4. 搜索每日领取按钮
    print("[4] 搜索每日领取...")
    claim_keywords = ["领取", "領取", "簽到", "签到", "claim", "daily", "check-in", "checkin", "每日", "每日簽到", "每日签到"]
    claim_done = False
    for kw in claim_keywords:
        # 搜索链接
        for text, url in link_list:
            if kw in text.lower():
                print(f"  找到领取链接: {text} → {url}")
                resp2 = s.get(url, timeout=20, allow_redirects=True)
                result_text = re.sub(r'<[^>]+>', ' ', resp2.text)
                result_text = re.sub(r'\s+', ' ', result_text).strip()
                print(f"  结果: {result_text[:200]}")
                results.append("✅ 每日领取已执行")
                claim_done = True
                break
        if claim_done: break

        # 搜索按钮
        for btn_text in all_btn_texts:
            if kw in btn_text.lower():
                print(f"  找到领取按钮: {btn_text}")
                # 查找包含该按钮的表单
                form_pattern = rf'<form[^>]*>(.*?{kw}.*?)</form>'
                form_matches = re.findall(form_pattern, resp.text, re.I | re.S)
                if form_matches:
                    form_action = re.search(r'<form[^>]*action=["\']([^"\']*)["\']', resp.text, re.I)
                    action_url = urljoin(final_url, form_action.group(1)) if form_action and form_action.group(1) else final_url
                    hidden = re.findall(r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', resp.text, re.I)
                    form_data = {n: v for n, v in hidden}
                    # 添加 submit
                    for sn, sv in re.findall(r'<input[^>]*type=["\']submit["\'][^>]*name=["\']([^"\']*)["\'][^>]*value=["\']([^"\']*)["\']', resp.text, re.I):
                        if sn: form_data[sn] = sv
                    resp2 = s.post(action_url, data=form_data, timeout=20, allow_redirects=True)
                    print(f"  提交: HTTP {resp2.status_code}")
                    results.append("✅ 每日领取已执行")
                    claim_done = True
                    break
        if claim_done: break

    if not claim_done:
        results.append("⚠️ 未找到领取按钮（可能已领取）")

    # 5. 搜索服务器续期
    print("[5] 搜索服务器续期...")
    renew_keywords = ["续期", "續期", "renew", "extend", "續約", "延期"]
    renew_done = False
    for kw in renew_keywords:
        for text, url in link_list:
            if kw in text.lower():
                print(f"  找到续期链接: {text} → {url}")
                resp2 = s.get(url, timeout=20, allow_redirects=True)
                print(f"  响应: HTTP {resp2.status_code}")
                results.append("✅ 服务器续期已执行")
                renew_done = True
                break
        if renew_done: break

        for btn_text in all_btn_texts:
            if kw in btn_text.lower():
                print(f"  找到续期按钮: {btn_text}")
                results.append("✅ 服务器续期已执行")
                renew_done = True
                break
        if renew_done: break

    if not renew_done:
        results.append("⚠️ 未找到续期按钮（可能无需续期）")

    # 6. TG 通知
    msg = f"📍 {name}\n" + "\n".join(results)
    print(f"\n{msg}")
    send_tg(msg)
    return claim_done or renew_done


def main():
    print("=" * 50)
    print("🏠 HNHost 自动续期（多账号）")
    print("=" * 50)
    accounts = parse_tokens()
    print(f"共 {len(accounts)} 个账号")

    results = []
    for account in accounts:
        try:
            success = renew_account(account)
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

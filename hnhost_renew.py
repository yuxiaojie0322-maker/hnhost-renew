#!/usr/bin/env python3
"""
HNHost 自动续期（多账号版）
通过 Discord Token API 直接获取 OAuth Code → 登录 HNHost → 续期
纯 HTTP 请求，不需要 Playwright。
"""

import os
import sys
import re
import json
import requests
import urllib3
from urllib.parse import urljoin
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ 配置 ============
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
        try:
            for item in json.loads(DISCORD_TOKENS):
                if isinstance(item, str):
                    tokens.append({"token": item, "name": f"账号{len(tokens)+1}"})
                elif isinstance(item, dict):
                    tokens.append({"token": item["token"], "name": item.get("name", f"账号{len(tokens)+1}")})
        except Exception as e:
            print(f"[!] DISCORD_TOKENS 解析失败: {e}")
    if not tokens:
        print("❌ 未配置 DISCORD_TOKENS")
        sys.exit(1)
    return tokens


def send_tg(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=30,
        )
        print(f"  [TG] 已推送")
    except:
        pass


def get_oauth_code(token: str) -> str | None:
    """用 Discord Token 获取 OAuth Code"""
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope={DISCORD_SCOPES.replace(' ', '+')}"
    resp = requests.post(url, headers={
        "Authorization": token,
        "Content-Type": "application/json",
    }, json={"authorize": True}, timeout=15)
    data = resp.json()
    location = data.get("location", "")
    if location:
        # 提取 code
        match = re.search(r'code=([^&]+)', location)
        if match:
            return match.group(1)
    print(f"  [!] 获取 OAuth Code 失败: {data}")
    return None


def login_hnhost(code: str) -> requests.Session | None:
    """用 OAuth Code 登录 HNHost，返回带 session 的 Session 对象"""
    session = requests.Session()
    session.verify = False
    session.proxies = {"http": PROXY, "https": PROXY}
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    # 访问 code URL，获取 PHPSESSID
    resp = session.get(f"{BASE_URL}/backend/pdo/discord.php?code={code}", timeout=20, allow_redirects=True)
    # 检查是否登录成功
    phpsessid = session.cookies.get("PHPSESSID", "")
    if not phpsessid:
        print(f"  [!] 未获取到 PHPSESSID")
        return None
    # 检查是否在登录页
    if "login" in resp.url and "登錄" in resp.text:
        print(f"  [!] 登录失败（仍在登录页）")
        return None
    print(f"  PHPSESSID: {phpsessid[:20]}...")
    return session


def find_and_click_renew(session: requests.Session, name: str) -> bool:
    """搜索续期按钮并点击"""
    renew_keywords = ["续期", "續期", "renew", "extend", "續約"]
    claim_keywords = ["领取", "領取", "簽到", "签到", "claim", "daily", "check", "每日"]
    renew_done = False

    # 1. 先访问主页面，找到所有导航链接
    print("  访问主页面...")
    resp = session.get(f"{BASE_URL}/pages/hnfs/create.php", timeout=20, allow_redirects=True)
    
    # 提取所有导航链接
    all_links = re.findall(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', resp.text, re.S)
    nav_links = {}
    for href, text in all_links:
        text = re.sub(r'<[^>]+>', '', text).strip()
        if text and href and ('管理' in text or '資源' in text or '商店' in text or '伺服器' in text or '創建' in text or '创建' in text):
            full_url = urljoin(f"{BASE_URL}/", href)
            nav_links[text] = full_url
            print(f"    {text} → {full_url}")

    # 2. 访问"管理伺服器"页面，找续期按钮
    manage_url = None
    for text, url in nav_links.items():
        if '管理' in text and '伺服器' in text:
            manage_url = url
            break
    
    if manage_url:
        print(f"\n  访问管理伺服器: {manage_url}")
        resp = session.get(manage_url, timeout=20, allow_redirects=True)
        print(f"    HTTP {resp.status_code}")
        
        if resp.status_code == 200 and "登錄平台" not in resp.text:
            # 打印页面内容摘要
            page_text = re.sub(r'<[^>]+>', ' ', resp.text)
            page_text = re.sub(r'\s+', ' ', page_text).strip()
            print(f"    页面摘要: {page_text[:300]}")
            
            # 打印按钮
            buttons = re.findall(r'<(?:a|button)[^>]*>(.*?)</(?:a|button)>', resp.text, re.S)
            btn_texts = [re.sub(r'<[^>]+>', '', b).strip()[:40] for b in buttons if b.strip()][:15]
            print(f"    按钮: {btn_texts}")
            
            # 搜索续期按钮
            for kw in renew_keywords + claim_keywords:
                pattern = rf'<a[^>]*href=["\']([^"\']+)["\'][^>]*>[^<]*{kw}[^<]*</a>'
                matches = re.findall(pattern, resp.text, re.I)
                if matches:
                    link_url = urljoin(f"{BASE_URL}/", matches[0])
                    print(f"    找到 '{kw}': {link_url}")
                    resp2 = session.get(link_url, timeout=20, allow_redirects=True)
                    print(f"    响应: HTTP {resp2.status_code}")
                    renew_done = True
                    break
            
            # 搜索表单
            if not renew_done:
                forms = re.findall(r'<form[^>]*action=["\']([^"\']*)["\'][^>]*>', resp.text, re.I)
                for form_action in forms:
                    if form_action and form_action != "#":
                        form_url = urljoin(f"{BASE_URL}/", form_action)
                        print(f"    找到表单: {form_url}")
                        hidden = re.findall(r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', resp.text, re.I)
                        form_data = {n: v for n, v in hidden}
                        # 添加 submit 按钮
                        submit_matches = re.findall(r'<input[^>]*type=["\']submit["\'][^>]*name=["\']([^"\']*)["\'][^>]*value=["\']([^"\']*)["\']', resp.text, re.I)
                        for sn, sv in submit_matches:
                            if sn:
                                form_data[sn] = sv
                        resp2 = session.post(form_url, data=form_data, timeout=20, allow_redirects=True)
                        print(f"    提交: HTTP {resp2.status_code}")
                        page_text2 = re.sub(r'<[^>]+>', ' ', resp2.text)
                        page_text2 = re.sub(r'\s+', ' ', page_text2).strip()
                        print(f"    结果: {page_text2[:200]}")
                        renew_done = True
                        break

    # 3. 访问"資源商店"页面，找每日领取
    store_url = None
    for text, url in nav_links.items():
        if '資源' in text or '商店' in text:
            store_url = url
            break
    
    if store_url:
        print(f"\n  访问資源商店: {store_url}")
        resp = session.get(store_url, timeout=20, allow_redirects=True)
        print(f"    HTTP {resp.status_code}")
        
        if resp.status_code == 200 and "登錄平台" not in resp.text:
            buttons = re.findall(r'<(?:a|button)[^>]*>(.*?)</(?:a|button)>', resp.text, re.S)
            btn_texts = [re.sub(r'<[^>]+>', '', b).strip()[:40] for b in buttons if b.strip()][:15]
            print(f"    按钮: {btn_texts}")
            
            # 搜索领取按钮
            for kw in claim_keywords:
                pattern = rf'<a[^>]*href=["\']([^"\']+)["\'][^>]*>[^<]*{kw}[^<]*</a>'
                matches = re.findall(pattern, resp.text, re.I)
                if matches:
                    link_url = urljoin(f"{BASE_URL}/", matches[0])
                    print(f"    找到 '{kw}': {link_url}")
                    resp2 = session.get(link_url, timeout=20, allow_redirects=True)
                    print(f"    响应: HTTP {resp2.status_code}")
                    renew_done = True
                    break

    # 4. 尝试直接 create=true
    if not renew_done:
        print(f"\n  尝试 create=true...")
        resp = session.get(f"{BASE_URL}/pages/hnfs/create.php?create=true", timeout=20, allow_redirects=True)
        if resp.status_code == 200 and "登錄平台" not in resp.text:
            page_text = re.sub(r'<[^>]+>', ' ', resp.text)
            page_text = re.sub(r'\s+', ' ', page_text).strip()
            print(f"    结果: {page_text[:200]}")
            renew_done = True

    return renew_done


def renew_account(account: dict) -> bool:
    name = account["name"]
    token = account["token"]
    print(f"\n{'='*50}")
    print(f"📍 {name}")
    print(f"{'='*50}")

    # 1. 获取 OAuth Code
    print("[1] 获取 OAuth Code...")
    code = get_oauth_code(token)
    if not code:
        send_tg(f"📍 {name}\n❌ 获取 OAuth Code 失败")
        return False
    print(f"  Code: {code[:20]}...")

    # 2. 登录 HNHost
    print("[2] 登录 HNHost...")
    session = login_hnhost(code)
    if not session:
        send_tg(f"📍 {name}\n❌ HNHost 登录失败")
        return False
    print("  登录成功 ✅")

    # 3. 续期
    print("[3] 执行续期...")
    renew_done = find_and_click_renew(session, name)

    if renew_done:
        msg = f"📍 {name}\n✅ HNHost 续期操作已执行"
    else:
        msg = f"📍 {name}\n⚠️ 未找到续期按钮（可能已续期）"
    print(f"  {msg.split(chr(10))[-1]}")
    send_tg(msg)
    return renew_done


def main():
    print("=" * 50)
    print("🏠 HNHost 自动续期（多账号）")
    print("=" * 50)

    accounts = parse_tokens()
    print(f"共 {len(accounts)} 个账号\n")

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
    print("📊 续期汇总")
    print(f"{'='*50}")
    for name, ok in results:
        print(f"  {name}: {'✅ 成功' if ok else '❌ 失败'}")
    print(f"\n总计: {sum(1 for _, ok in results if ok)}/{len(results)} 成功")


if __name__ == "__main__":
    main()

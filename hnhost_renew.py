#!/usr/bin/env python3
"""
HNHost 自动续期脚本
通过 sing-box Hysteria2 代理 + PHPSESSID Cookie 访问面板。
流程: 代理连接 → Cookie登录 → 访问 create.php → 提交续期 → TG通知
"""

import asyncio
import os
import sys
import re
import requests
import urllib3
from urllib.parse import urljoin
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ 配置 ============
HNHOST_COOKIE = os.environ.get("HNHOST_COOKIE", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "1080")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

BASE_URL = "https://client.hnhost.net"
PROXY = f"socks5h://127.0.0.1:{PROXY_PORT}"

# ============ Session ============
session = requests.Session()
session.verify = False
session.proxies = {"http": PROXY, "https": PROXY}
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})
if HNHOST_COOKIE:
    session.headers["Cookie"] = HNHOST_COOKIE


# ============ TG 推送 ============
def send_tg(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print(f"[TG] 未配置，跳过")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=30,
        )
        print(f"[TG] 已推送")
    except Exception as e:
        print(f"[TG] 异常: {e}")


# ============ 主流程 ============
def main():
    print("=" * 50)
    print("🏠 HNHost 自动续期")
    print("=" * 50)

    if not HNHOST_COOKIE:
        print("❌ 未配置 HNHOST_COOKIE")
        send_tg("❌ HNHost: 未配置 Cookie")
        sys.exit(1)

    print(f"[Config] 代理: socks5://127.0.0.1:{PROXY_PORT}")
    print(f"[Config] Cookie: {HNHOST_COOKIE[:30]}...\n")

    results = []

    # 1. 测试代理和面板连通性
    print("[1] 测试连接...")
    try:
        resp = session.get(f"{BASE_URL}/login.php", timeout=20, allow_redirects=False)
        print(f"    面板 HTTP {resp.status_code}")
    except Exception as e:
        print(f"    ❌ 连接失败: {e}")
        send_tg("❌ HNHost: 代理连接失败")
        sys.exit(1)

    # 2. 检查是否已登录（访问 dashboard 看是否跳转）
    print("[2] 检查登录状态...")
    resp = session.get(f"{BASE_URL}/pages/hnfs/index.php", timeout=20, allow_redirects=False)
    if resp.status_code == 302:
        location = resp.headers.get("location", "")
        if "login" in location:
            print("    ❌ Cookie 已过期，需要重新登录")
            send_tg("❌ HNHost Cookie 已过期\n请重新登录获取新 Cookie")
            sys.exit(1)
    print("    Cookie 有效 ✅")
    results.append("✅ 登录成功")

    # 3. 访问 create.php 页面
    print("[3] 访问创建/续期页面...")
    resp = session.get(f"{BASE_URL}/pages/hnfs/create.php", timeout=20, allow_redirects=True)
    print(f"    HTTP {resp.status_code}, URL: {resp.url}")

    if resp.status_code != 200:
        print(f"    ❌ 无法访问 create.php")
        results.append("❌ 无法访问续期页面")
        send_tg("❌ HNHost: 无法访问续期页面")
        sys.exit(1)

    # 打印页面按钮/表单信息
    buttons = re.findall(r'<(?:a|button|input)[^>]*(?:href|value|onclick)[^>]*>[^<]*', resp.text, re.I)
    btn_texts = [re.sub(r'<[^>]+>', '', b).strip()[:50] for b in buttons if b.strip()][:20]
    print(f"    页面按钮: {btn_texts}")

    forms = re.findall(r'<form[^>]*action=["\']([^"\']*)["\'][^>]*>', resp.text, re.I)
    print(f"    表单: {forms}")

    # 搜索续期/创建按钮
    renew_keywords = ["续期", "续费", "创建", "續期", "renew", "create", "extend", "claim", "领取", "簽到"]
    renew_done = False

    for kw in renew_keywords:
        # 查找链接
        pattern = rf'<(?:a|button)[^>]*(?:href|onclick)[^>]*>[^<]*{kw}[^<]*</(?:a|button)>'
        matches = re.findall(pattern, resp.text, re.I)
        if matches:
            for m in matches[:3]:
                href_match = re.search(r'href=["\']([^"\']*)["\']', m, re.I)
                onclick_match = re.search(r"onclick=[\"']([^\"']*)[\"']", m, re.I)
                if href_match:
                    url = urljoin(f"{BASE_URL}/", href_match.group(1))
                    print(f"    找到 '{kw}' 链接: {url}")
                    resp2 = session.get(url, timeout=20, allow_redirects=True)
                    print(f"    响应: HTTP {resp2.status_code}")
                    renew_done = True
                    break
                elif onclick_match:
                    print(f"    找到 '{kw}' 按钮: {onclick_match.group(1)[:50]}")
                    renew_done = True
                    break
            if renew_done:
                break

    # 查找表单提交
    if not renew_done:
        for form_action in forms:
            if any(kw in form_action.lower() for kw in ["create", "renew", "submit"]):
                form_url = urljoin(f"{BASE_URL}/", form_action)
                print(f"    找到表单: {form_url}")

                # 提取隐藏字段
                hidden = re.findall(
                    r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
                    resp.text, re.I
                )
                form_data = {n: v for n, v in hidden}
                print(f"    隐藏字段: {list(form_data.keys())}")

                resp2 = session.post(form_url, data=form_data, timeout=20, allow_redirects=True)
                print(f"    表单提交: HTTP {resp2.status_code}")
                renew_done = True
                break

    # 直接尝试 create.php?create=true
    if not renew_done:
        print("    尝试直接 create=true...")
        resp2 = session.get(f"{BASE_URL}/pages/hnfs/create.php?create=true", timeout=20, allow_redirects=True)
        print(f"    响应: HTTP {resp2.status_code}")
        if resp2.status_code == 200:
            renew_done = True

    # 4. 访问 renew.php
    print("[4] 检查 renew.php...")
    resp3 = session.get(f"{BASE_URL}/pages/hnfs/renew.php", timeout=20, allow_redirects=True)
    print(f"    HTTP {resp3.status_code}")

    if resp3.status_code == 200:
        renew_buttons = re.findall(r'<(?:a|button)[^>]*>([^<]*)</(?:a|button)>', resp3.text, re.S)
        renew_texts = [re.sub(r'<[^>]+>', '', b).strip()[:50] for b in renew_buttons if b.strip()][:10]
        print(f"    renew.php 按钮: {renew_texts}")

        for kw in renew_keywords:
            pattern = rf'<(?:a|button)[^>]*(?:href|onclick)[^>]*>[^<]*{kw}[^<]*</(?:a|button)>'
            matches = re.findall(pattern, resp3.text, re.I)
            if matches:
                for m in matches[:3]:
                    href_match = re.search(r'href=["\']([^"\']*)["\']', m, re.I)
                    if href_match:
                        url = urljoin(f"{BASE_URL}/", href_match.group(1))
                        print(f"    找到 '{kw}' 链接: {url}")
                        session.get(url, timeout=20, allow_redirects=True)
                        renew_done = True
                        break
                if renew_done:
                    break

    # 5. 结果
    if renew_done:
        results.append("✅ 续期操作已执行")
    else:
        results.append("⚠️ 未找到续期按钮（可能已续期）")

    # 6. 检查最终状态
    print("[5] 检查最终状态...")
    resp_final = session.get(f"{BASE_URL}/pages/hnfs/status.php", timeout=20, allow_redirects=True)
    if resp_final.status_code == 200:
        # 提取状态信息
        status_text = re.sub(r'<[^>]+>', ' ', resp_final.text)
        status_text = re.sub(r'\s+', ' ', status_text).strip()
        # 找关键词
        for kw in ["active", "running", "expire", "到期", "剩余", "days", "天"]:
            if kw.lower() in status_text.lower():
                idx = status_text.lower().find(kw.lower())
                snippet = status_text[max(0,idx-20):idx+40]
                print(f"    状态: ...{snippet}...")
                break

    msg = "🏠 *HNHost 自动续期*\n" + "\n".join(results)
    print(f"\n{msg}")
    send_tg(msg)
    print("\n✅ 任务完成")


if __name__ == "__main__":
    main()

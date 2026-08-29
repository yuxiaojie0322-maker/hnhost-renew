#!/usr/bin/env python3
"""
HNHost 自动续期脚本
通过 Discord Token 自动完成 OAuth 登录，获取 PHPSESSID，执行续期。
全程自动化，无需手动更新 Cookie。

流程: Discord Token → OAuth自动登录 → 获取Session → create.php续期 → TG通知
"""

import asyncio
import os
import sys
import re
import json
from pathlib import Path
from playwright.async_api import async_playwright

# ============ 配置 ============
DISCORD_TOKENS = os.environ.get("DISCORD_TOKENS", "")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")  # 兼容单账号
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
HEADLESS = os.environ.get("HEADLESS", "true") == "true"
SCREENSHOT_DIR = Path("/tmp/hnhost_screenshots")

BASE_URL = "https://client.hnhost.net"
DISCORD_LOGIN_URL = f"{BASE_URL}/backend/pdo/discord.php?action=login"


# ============ TG 推送 ============
async def send_tg(text: str, photo: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print(f"[TG] 未配置，跳过")
        return
    import aiohttp
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"
    async with aiohttp.ClientSession() as sess:
        if photo and os.path.exists(photo):
            form = aiohttp.FormData()
            form.add_field("chat_id", TG_CHAT_ID)
            form.add_field("caption", text)
            form.add_field("photo", open(photo, "rb"), filename="screenshot.jpg")
            await sess.post(f"{url}/sendPhoto", data=form)
        else:
            await sess.post(f"{url}/sendMessage", json={
                "chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"
            })
    print(f"[TG] 已推送")


def parse_tokens():
    """解析多账号 token 列表"""
    tokens = []
    if DISCORD_TOKENS:
        try:
            data = json.loads(DISCORD_TOKENS)
            for item in data:
                if isinstance(item, str):
                    tokens.append({"token": item, "name": f"账号{len(tokens)+1}"})
                elif isinstance(item, dict):
                    tokens.append({
                        "token": item["token"],
                        "name": item.get("name", f"账号{len(tokens)+1}")
                    })
        except Exception as e:
            print(f"[!] DISCORD_TOKENS 解析失败: {e}")
    if DISCORD_TOKEN and not tokens:
        tokens.append({"token": DISCORD_TOKEN, "name": "账号1"})
    if not tokens:
        print("❌ 未配置 DISCORD_TOKENS 或 DISCORD_TOKEN")
        sys.exit(1)
    return tokens


# ============ 单账号续期 ============
async def renew_account(account: dict, p) -> bool:
    token = account["token"]
    name = account["name"]
    print(f"\n{'='*50}")
    print(f"📍 开始处理: {name}")
    print(f"{'='*50}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            proxy={"server": "socks5://127.0.0.1:1080"},
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = await ctx.new_page()

        # 用 add_init_script 在所有页面加载前注入 Discord token
        await ctx.add_init_script(f"""
            try {{ localStorage.setItem('token', JSON.stringify('{token}')); }} catch(e) {{}}
        """)
        print(f"\n[1] Discord Token 已注入（init_script）")

        try:
            # 2. 直接跳转 HNHost OAuth 登录
            print("[2] OAuth 自动登录...")
            await page.goto(DISCORD_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)

            current_url = page.url
            print(f"    当前 URL: {current_url}")

            # 检查是否在 Discord 授权页面
            if "discord.com" in current_url:
                print("    在 Discord 授权页面，等待自动跳转...")
                # 尝试自动点击授权按钮（如果有的话）
                for i in range(15):
                    await page.wait_for_timeout(2000)
                    current_url = page.url
                    if "client.hnhost.net" in current_url:
                        print(f"    已跳回 HNHost ({i*2}s)")
                        break
                    # 尝试点击 "Authorize" 按钮
                    clicked = await page.evaluate("""
                        () => {
                            const btns = document.querySelectorAll('button');
                            for (const b of btns) {
                                if (b.textContent.includes('Authorize') || b.textContent.includes('authorize') || b.textContent.includes('授权')) {
                                    b.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    if clicked:
                        print(f"    点击了授权按钮 ({i*2}s)")
                    if i % 5 == 0:
                        print(f"    等待... {i*2}s, URL: {current_url[:60]}")

                await page.wait_for_timeout(3000)
                current_url = page.url

            # 3. 检查是否登录成功
            print("[3] 检查登录状态...")
            current_url = page.url
            print(f"    URL: {current_url}")

            if "login" in current_url:
                print("    ❌ 登录失败（Token 无效或 OAuth 被拒）")
                await page.screenshot(path=str(SCREENSHOT_DIR / "login_fail.png"))
                await send_tg("❌ HNHost 登录失败\nDiscord Token 可能无效")
                return

            # 获取 cookies
            cookies = await ctx.cookies()
            phpsessid = None
            for c in cookies:
                if c["name"] == "PHPSESSID":
                    phpsessid = c["value"]
                    break
            print(f"    PHPSESSID: {phpsessid[:20] + '...' if phpsessid else '未找到'}")
            print("    登录成功 ✅")

            # 4. 访问创建/续期页面
            print("[4] 访问续期页面...")

            # 先访问 index 看有什么
            await page.goto(f"{BASE_URL}/pages/hnfs/index.php", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            page_text = await page.inner_text("body")
            print(f"    index 页面文字: {page_text[:300]}")

            # 搜索所有链接
            links = await page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('a')).map(a => ({
                        text: a.textContent.trim().substring(0, 50),
                        href: a.href
                    })).filter(l => l.text && l.href);
                }
            """)
            print(f"    页面链接: {len(links)} 个")
            for link in links[:10]:
                print(f"      {link['text']} → {link['href'][:60]}")

            # 访问 create.php
            print("\n    访问 create.php...")
            await page.goto(f"{BASE_URL}/pages/hnfs/create.php", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            page_text = await page.inner_text("body")
            print(f"    create.php 文字: {page_text[:300]}")

            # 搜索按钮
            buttons = await page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('button, input[type="submit"], a.btn')).map(b => ({
                        text: b.textContent.trim().substring(0, 50) || b.value || '',
                        onclick: b.onclick ? 'yes' : 'no',
                        href: b.href || '',
                    })).filter(b => b.text);
                }
            """)
            print(f"    按钮: {buttons}")

            # 访问 renew.php
            print("\n    访问 renew.php...")
            await page.goto(f"{BASE_URL}/pages/hnfs/renew.php", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            page_text = await page.inner_text("body")
            print(f"    renew.php 文字: {page_text[:300]}")

            renew_buttons = await page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('button, input[type="submit"], a.btn')).map(b => ({
                        text: b.textContent.trim().substring(0, 50) || b.value || '',
                        onclick: b.getAttribute('onclick') || '',
                        href: b.href || '',
                    })).filter(b => b.text);
                }
            """)
            print(f"    renew 按钮: {renew_buttons}")

            # 5. 尝试续期
            print("[5] 执行续期...")
            renew_done = False

            # 在 create.php 和 renew.php 中搜索续期按钮
            for page_url in [f"{BASE_URL}/pages/hnfs/create.php", f"{BASE_URL}/pages/hnfs/renew.php"]:
                await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)

                # 搜索各种续期关键词
                keywords = ["续期", "續期", "创建", "創建", "renew", "create", "extend", "claim", "领取", "簽到", "签到", "submit", "確認"]
                for kw in keywords:
                    clicked = await page.evaluate(f"""
                        () => {{
                            const els = document.querySelectorAll('button, a, input[type="submit"]');
                            for (const el of els) {{
                                const text = (el.textContent || el.value || '').trim();
                                if (text.includes('{kw}') || text.toLowerCase().includes('{kw.lower()}')) {{
                                    el.click();
                                    return true;
                                }}
                            }}
                            return false;
                        }}
                    """)
                    if clicked:
                        print(f"    点击了 '{kw}' 按钮")
                        await page.wait_for_timeout(5000)
                        result_text = await page.inner_text("body")
                        print(f"    结果: {result_text[:200]}")
                        await page.screenshot(path=str(SCREENSHOT_DIR / "renew_result.png"))
                        renew_done = True
                        break
                if renew_done:
                    break

            # 尝试直接 create=true
            if not renew_done:
                print("    尝试 create=true...")
                await page.goto(f"{BASE_URL}/pages/hnfs/create.php?create=true", wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                page_text = await page.inner_text("body")
                print(f"    结果: {page_text[:200]}")
                if "success" in page_text.lower() or "成功" in page_text:
                    renew_done = True

            # 6. 结果
            if renew_done:
                msg = "✅ HNHost 续期操作已执行"
            else:
                # 截图最终状态
                await page.screenshot(path=str(SCREENSHOT_DIR / "final.png"))
                msg = "⚠️ HNHost 未找到续期按钮（可能已续期或页面结构不同）"

            print(f"\n{msg}")
            await send_tg(f"📍 {name}\n" + msg, str(SCREENSHOT_DIR / "renew_result.png") if renew_done else str(SCREENSHOT_DIR / "final.png"))
            return renew_done

        except Exception as e:
            print(f"[{name}] [!] 异常: {e}")
            import traceback
            traceback.print_exc()
            try:
                await page.screenshot(path=str(SCREENSHOT_DIR / f"{name}_error.png"))
                await send_tg(f"📍 {name}\n❌ 异常: {e}", str(SCREENSHOT_DIR / f"{name}_error.png"))
            except:
                pass
            return False
        finally:
            await browser.close()


# ============ 主流程 ============
async def main():
    print("=" * 50)
    print("🏠 HNHost 自动续期（多账号）")
    print("=" * 50)

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    accounts = parse_tokens()
    print(f"共 {len(accounts)} 个账号\n")

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        results = []
        for account in accounts:
            success = await renew_account(account, p)
            results.append((account["name"], success))
            await asyncio.sleep(3)

    print(f"\n{'='*50}")
    print("📊 续期汇总")
    print(f"{'='*50}")
    for name, ok in results:
        print(f"  {name}: {'✅ 成功' if ok else '❌ 失败'}")
    print(f"\n总计: {sum(1 for _, ok in results if ok)}/{len(results)} 成功")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全自动开启 TOTP 2FA + 输出 账号----密码----TOTP secret。

流程:
  1. 密码账号 cookies 进安全页
  2. dispatch 鼠标事件点击 mfa-authenticator-toggle(React switch 需要完整事件序列)
  3. 新鲜会话(recent_auth 满足)→ 直接进 TOTP 设置;否则密码/邮箱 re-auth
  4. 抓 TOTP secret → 输出

用法: python capture/enable_totp.py [--email 密码账号] [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402
from urllib.parse import urlparse, unquote  # noqa: E402

# Windows 控制台 GBK 无法打印 emoji,强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _find_account(email_contains: str = "QuentinKaboos152") -> dict:
    lines = [l for l in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for d in [json.loads(l) for l in lines]:
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


def _find_mail_account(main_email: str) -> dict:
    base = main_email.split("@")[0].split("+")[0] + "@" + main_email.split("@")[1]
    for line in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        a = parse_mail_line(line)
        if a and a["email"].split("@")[0].split("+")[0] + "@" + a["email"].split("@")[1] == base:
            return a
    raise RuntimeError(f"号池找不到主号 {base}")


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="QuentinKaboos152")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    email = acc["email"]
    password = acc.get("password") or ""
    main_email = email.split("+")[0] + "@" + email.split("@")[1]
    print(f"账号: {email}  密码: {password[:4]}***  主号: {main_email}")

    from playwright.sync_api import sync_playwright

    r = resolve_proxy(cfg, override=args.proxy)
    pp = urlparse(r.session_url)
    pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
    if pp.username:
        pw["username"] = unquote(pp.username)
        pw["password"] = unquote(pp.password or "")
    print(f"代理: {r.label()}")

    with sync_playwright() as p:
        b = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy=pw,
        )
        ctx = b.new_context(
            user_agent=cfg.get("browser", {}).get("user_agent"),
            locale="en-US", viewport={"width": 1920, "height": 1080},
        )
        for c in (acc.get("session_cookies") or []):
            for d in (c.get("domain", "").lstrip("."), c.get("domain", "")):
                if not d:
                    continue
                try:
                    ctx.add_cookies([{"name": c["name"], "value": c["value"], "domain": d,
                                      "path": c.get("path") or "/",
                                      "secure": bool(c.get("secure", False))}])
                except Exception:
                    pass
        page = ctx.new_page()

        # 1. 打开安全页
        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector("[data-testid=mfa-authenticator-toggle]", timeout=30000)
        except Exception:
            print("[!] 安全页加载失败(或该账号无 MFA 区域)")
            b.close()
            r.close()
            return 2
        page.wait_for_timeout(2000)
        print(f"安全页 URL: {page.url}")

        # 2. dispatch 鼠标事件点击开关(React switch 需要完整事件序列)
        page.evaluate("""() => {
            const el = document.querySelector('[data-testid=mfa-authenticator-toggle]');
            if (el) {
                for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                }
            }
        }""")
        print("[1] dispatch 鼠标事件点击开关")
        # 轮询等待 TOTP 设置(scan the qr)最多 15s(React 渲染需时)
        txt = ""
        direct_totp = False
        for _ in range(15):
            page.wait_for_timeout(1000)
            try:
                t_direct = page.evaluate("() => document.body.innerText")
                if "scan the qr" in t_direct.lower() or "enter your 6-digit" in t_direct.lower():
                    txt = t_direct
                    direct_totp = True
                    print("[2] 直接进入 TOTP 设置,跳过 re-auth!")
                    break
            except Exception:
                pass
        if not direct_totp:
            # 3. re-auth:优先密码(Continue with password),无则邮箱验证码
            print(f"[2] re-auth UI: {page.url[:60]}")
            cwp = page.locator("text=Continue with password").first
            if not cwp.count():
                try:
                    page.wait_for_selector("text=Continue with password", timeout=8000)
                except Exception:
                    pass
                cwp = page.locator("text=Continue with password").first
            if cwp.count():
                cwp.click(force=True)
                print("[3] 点击 Continue with password")
            else:
                print("[3] 无 Continue with password,走邮箱验证码")
                otp_after = time.time()
                mail_account = _find_mail_account(main_email)
                client = build_mail_client(mail_account, proxy=r.session_url or None,
                                           impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
                otp = client.wait_for_otp(after_ts=otp_after,
                                          timeout=max(int(cfg.get("mail", {}).get("max_wait", 90)), 150),
                                          interval=3, settle_seconds=5)
                print(f"  OTP: {otp}")
                code_el = page.locator("input[name=code]").first
                if code_el.count():
                    code_el.fill(otp)
                    try:
                        page.locator("button:has-text('Continue')").first.click()
                    except Exception:
                        page.keyboard.press("Enter")
            # 4. 填密码(如果走了密码路径)
            for _ in range(8):
                pw_el = page.locator("input[type=password], input[name=password]").first
                if pw_el.count():
                    pw_el.fill(password)
                    print("[4] 填密码")
                    try:
                        page.locator("button:has-text('Continue')").first.click()
                    except Exception:
                        page.keyboard.press("Enter")
                    break
                page.wait_for_timeout(1000)
            # 5. 等回调完成
            for _ in range(30):
                page.wait_for_timeout(1000)
                url = page.url
                if "chatgpt.com" in url and "callback" not in url and "auth.openai" not in url:
                    break
            print(f"[5] 回调后 URL: {page.url[:80]}")
            # 6. 导航 action=enable URL
            page.goto("https://chatgpt.com/?action=enable&factor=totp#settings/Security",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(8000)
            # 7. 等 TOTP 设置
            txt = ""
            for _ in range(15):
                page.wait_for_timeout(1000)
                try:
                    cur = page.evaluate("() => document.body.innerText")
                    if cur and len(cur.strip()) > 20:
                        txt = cur
                        if "scan the qr" in cur.lower() or "enter your 6-digit" in cur.lower() \
                                or "verification code" in cur.lower() or "authenticator" in cur.lower():
                            break
                except Exception:
                    pass

        print(f"[TOTP 设置] URL={page.url} body {len(txt)} 字符:")
        if txt:
            print(txt[300:1500].replace(chr(10), " | "))

        # 8. 抓 secret(Trouble scanning 显示 base32 key)
        secret = None
        # 8a. 文本里的 otpauth/base32
        m_otp = re.search(r"otpauth://[^\s\"']+", txt)
        m_sec = re.search(r"[A-Z2-7]{32}", txt)
        if m_otp:
            secret = m_otp.group(0)
            m2 = re.search(r"[?&]secret=([A-Z2-7]+)", secret)
            if m2:
                secret = m2.group(1)
        elif m_sec:
            secret = m_sec.group(0)
        # 8b. 点 Trouble scanning 显示 base32 secret(二维码里的 secret 只在文本显示)
        if not secret:
            try:
                ts = page.locator("text=Trouble scanning").first
                if ts.count():
                    ts.click()
                    page.wait_for_timeout(2500)
                    txt2 = page.evaluate("() => document.body.innerText")
                    m_sec2 = re.search(r"[A-Z2-7]{32}", txt2)
                    if m_sec2:
                        secret = m_sec2.group(0)
                        print(f"[8] Trouble scanning secret 抓取成功")
            except Exception as exc:
                print(f"Trouble scanning 失败: {exc}")
        if not secret:
            print("[!] 未能抓取 TOTP secret")
            b.close()
            r.close()
            return 3

        print(f"\n🎉 TOTP secret: {secret}")

        # 9. pyotp 生成 6 位码 → 填 Step 2 → Verify(完成开启)
        try:
            import pyotp

            totp = pyotp.TOTP(secret)
            code6 = totp.now()
            print(f"[9] 生成 6 位码: {code6}")
            # 填 Step 2 验证码
            filled = False
            for _ in range(6):
                for sel in ("input[name=code]", "input[autocomplete=one-time-code]",
                            "input[type=text]", "input"):
                    try:
                        el = page.locator(sel).first
                        if el.count():
                            el.fill(code6)
                            filled = True
                            break
                    except Exception:
                        pass
                if filled:
                    break
                page.wait_for_timeout(1000)
            if filled:
                try:
                    v = page.locator("button:has-text('Verify')").first
                    if v.count():
                        v.click()
                        print("[9] 点击 Verify")
                    else:
                        page.keyboard.press("Enter")
                except Exception:
                    page.keyboard.press("Enter")
                page.wait_for_timeout(5000)
                try:
                    t3 = page.evaluate("() => document.body.innerText")
                    if "enabled" in t3.lower() or "authenticator" in t3.lower():
                        print("[9] ✅ TOTP 已启用确认")
                except Exception:
                    pass
            else:
                print("[9] 未找到 Step 2 验证码输入框")
        except Exception as exc:
            print(f"[9] 验证步骤异常: {exc}")

        # 10. 输出
        print("\n" + "=" * 50)
        print(f"账号: {email}")
        print(f"密码: {password}")
        print(f"TOTP: {secret}")
        print(f"otpauth: otpauth://totp/ChatGPT:{email}?secret={secret}&issuer=ChatGPT")
        print("=" * 50)
        out = ROOT / "output" / "totp_accounts.txt"
        with out.open("a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{secret}\n")
        print(f"已保存 {out}")
        b.close()
        r.close()
        return 0

        b.close()
    r.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

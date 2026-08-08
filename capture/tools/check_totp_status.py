#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查 TOTP 2FA 实际激活状态 + 抓 enroll/confirm 真实 API 结构。

背景: probe_totp_login 发现 password/verify 后直接给 code(登录不要求 TOTP) →
    猜测 enroll 只登记未激活(缺 confirm 步骤)。本脚本用浏览器打开安全页看真相:
  - 若 Authenticator app 已启用 → enroll 激活了, 登录流设计如此
  - 若显示可开启 → 缺 confirm, 点开后监听网络请求抓 mfa/enroll + confirm 的 API 结构

流程: 注入已存 cookies → 打开 chatgpt.com/settings/security → 检查登录态 →
  (被登出则 re-auth: Continue with password 优先, 邮箱 OTP 兜底) → dump MFA 状态 →
  若可开启则监听 /backend-api/accounts/mfa/* 网络请求并点击。

用法: python capture/check_totp_status.py [--email 账号关键字] [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402


def _find_account(email_contains: str) -> dict:
    for line in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
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


def _dump_state(page, tag: str) -> str:
    txt = ""
    try:
        txt = page.evaluate("() => document.body.innerText")
    except Exception:
        pass
    print(f"  [{tag}] URL={page.url[:80]} body={len(txt)}字符")
    return txt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="KathrynEverett6196")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    ap.add_argument("--click", action="store_true",
                    help="发现可开启时点开 Authenticator app 抓 enroll/confirm API(默认只检查状态)")
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

    mfa_reqs = []  # 抓 /backend-api/accounts/mfa/* 请求

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
        page.on("request", lambda req: mfa_reqs.append(
            {"method": req.method, "url": req.url, "post": req.post_data[:400] if req.post_data else None})
            if "mfa" in req.url or "totp" in req.url else None)
        t0 = time.time()

        # 1. 打开安全页
        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        txt = _dump_state(page, "安全页")

        # 2. 检查登录态: 是否跳到 auth.openai(未登录)
        logged_in = "chatgpt.com" in page.url and "auth.openai" not in page.url
        if not logged_in:
            print("[!] 未登录(cookies 过期), 走 re-auth")
            print(f"    URL: {page.url[:90]}")
            # 2a. 找 Continue with password
            cwp = page.locator("text=Continue with password").first
            if cwp.count():
                cwp.click(force=True)
                page.wait_for_timeout(3000)
                print(f"    点击 Continue with password → {page.url[:60]}")
            # 2b. 找邮箱输入框(可能直接要邮箱)
            else:
                mail_el = page.locator("input[name=email], input[type=email], input[name=username]").first
                if mail_el.count():
                    mail_el.fill(email)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(3000)
                    print(f"    填邮箱提交 → {page.url[:60]}")
            # 2c. 填密码
            for _ in range(6):
                pw_el = page.locator("input[type=password], input[name=password]").first
                if pw_el.count():
                    pw_el.fill(password)
                    print("    填密码")
                    try:
                        page.locator("button:has-text('Continue')").first.click()
                    except Exception:
                        page.keyboard.press("Enter")
                    break
                page.wait_for_timeout(1000)
            # 2d. 若要求邮箱 OTP(密码账号无 2FA, 可能密码直接过; 兜底收 OTP)
            page.wait_for_timeout(4000)
            cur = page.url
            if "email-otp" in cur or "email-verification" in cur or "code" in cur.lower():
                print("    需要邮箱 OTP")
                otp_after = time.time()
                mail_account = _find_mail_account(main_email)
                client = build_mail_client(mail_account, proxy=r.session_url or None,
                                           impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
                otp = client.wait_for_otp(after_ts=otp_after, timeout=150, interval=3, settle_seconds=5)
                print(f"    OTP: {otp}")
                code_el = page.locator("input[name=code], input[autocomplete=one-time-code]").first
                if code_el.count():
                    code_el.fill(otp)
                    try:
                        page.locator("button:has-text('Continue')").first.click()
                    except Exception:
                        page.keyboard.press("Enter")
                    page.wait_for_timeout(5000)
            # 2e. 回到安全页
            page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            txt = _dump_state(page, "re-auth 后安全页")

        # 3. fetch mfa_info(权威 MFA 状态, 页面已登录态直接调)
        try:
            mi = page.evaluate("""async () => {
                const r = await fetch('/backend-api/accounts/mfa_info', {credentials: 'include'});
                return {status: r.status, body: await r.text()};
            }""")
            print("\n=== mfa_info(权威 MFA 状态) ===")
            print(str(mi)[:2000])
        except Exception as exc:
            print(f"  mfa_info fetch 失败: {exc}")

        # 4. dump MFA 相关区域
        print("\n=== MFA/2FA 关键词 ===")
        for kw in ("Authenticator", "two-factor", "2FA", "TOTP", "one-time", "verification",
                   "Advanced account security", "enabled", "Set up", "Enable", "passkey"):
            idx = txt.find(kw)
            if idx >= 0:
                print(f"  含[{kw}] @{idx}: ...{txt[max(0,idx-30):idx+90].strip()}...")
        # 3b. dump 按钮
        try:
            btns = page.eval_on_selector_all(
                "button", "els => els.map(e => (e.innerText||'').trim().slice(0,50)).filter(x=>x)")
            print("\n按钮:", btns[:20])
        except Exception:
            pass

        # 5. 若 Authenticator 可开启(未激活) → 点开抓 enroll API(--click 才做)
        look_enable = [kw for kw in ("Set up", "Enable", "Turn on") if kw in txt]
        if look_enable and args.click:
            print(f"\n[!] 发现可开启关键词 {look_enable} → 尝试点开 Authenticator app 抓 API")
            # 点包含 authenticator/2FA 的开启按钮
            clicked = False
            for sel_txt in ("Authenticator app", "authenticator", "two-factor", "2FA"):
                try:
                    loc = page.locator(f"text={sel_txt}").first
                    if loc.count():
                        loc.click(force=True)
                        print(f"  点击 [{sel_txt}]")
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                page.wait_for_timeout(5000)
                txt2 = _dump_state(page, "点击后")
                try:
                    btns2 = page.eval_on_selector_all(
                        "button", "els => els.map(e => (e.innerText||'').trim().slice(0,50)).filter(x=>x)")
                    print("  点击后按钮:", btns2[:15])
                except Exception:
                    pass
        else:
            print("\n[!] 未发现可开启关键词 → 状态: Authenticator 已启用(或不在该页)")

        print(f"\n=== 捕获的 mfa/totp 网络请求({len(mfa_reqs)}) ===")
        for m in mfa_reqs:
            print(f"  {m['method']} {m['url']}")
            if m.get("post"):
                print(f"    post: {m['post']}")

        print(f"\n[总耗时] {(time.time()-t0):.1f}s")
        b.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

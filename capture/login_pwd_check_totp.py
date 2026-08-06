#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码账号登录 + 检查安全页是否有 TOTP 2FA 选项。

关键验证:register 创建的密码账号能否用密码登录,安全页是否有"验证器应用(TOTP)"。
若 TOTP 可开 → 全自动 2FA + 输出 secret 可行。

用法: python capture/login_pwd_check_totp.py [--email x] [--password x] [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from urllib.parse import urlparse, unquote  # noqa: E402


def _latest_pwd_account() -> dict:
    lines = [l for l in (ROOT / "data" / "pwd_accounts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        raise RuntimeError("无密码账号,先跑 verify_pwd_v3")
    return json.loads(lines[-1])


def _dump(page, tag):
    try:
        els = page.eval_on_selector_all(
            "input, button",
            "els => els.map(e => ({t:e.tagName, ty:e.type, n:e.name||'', ph:e.placeholder||'', "
            "tx:(e.innerText||'').trim().slice(0,25)}))",
        )
        print(f"  [{tag}] URL={page.url[:90]}")
        for e in els[:12]:
            print(f"      <{e['t']} type={e['ty']} name={e['n']} ph={e['ph']} tx={e['tx']}>")
    except Exception as exc:
        print(f"  [{tag}] dump 失败: {exc}")


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    acc = _latest_pwd_account()
    email = args.email or acc["email"]
    password = args.password or acc["password"]
    print(f"密码账号: {email}  密码: {password}")

    cfg = load_config()
    r = resolve_proxy(cfg, override=args.proxy)
    pp = urlparse(r.session_url)
    pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
    if pp.username:
        pw["username"] = unquote(pp.username)
        pw["password"] = unquote(pp.password or "")
    print(f"代理: {r.label()}")

    from playwright.sync_api import sync_playwright

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
        page = ctx.new_page()

        # 1. 打开登录页
        page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        _dump(page, "登录页")

        # 2. 填邮箱
        filled = False
        for sel in ("input[name=email]", "input[type=email]", "input[name=username]"):
            try:
                el = page.locator(sel).first
                if el.count():
                    el.fill(email)
                    filled = True
                    print(f"  [邮箱] 填 {email} via {sel}")
                    break
            except Exception:
                pass
        if not filled:
            try:
                page.locator("input").first.fill(email)
                print("  [邮箱] 填第一个 input")
            except Exception as exc:
                print(f"  填邮箱失败: {exc}")
                return 2
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(4000)
        _dump(page, "提交邮箱后")

        # 3. email-verification 页:收验证码登录(密码账号可用 OTP 登录,登录后看安全页 TOTP)
        import time as _time
        from gptreg.mail.pool import parse_mail_line as _pml
        from gptreg.mail.providers import build_mail_client as _bmc

        otp_after = _time.time()
        # 从号池找主号收码
        main = acc["mail_main"] or acc["email"]
        base = main.split("@")[0].split("+")[0] + "@" + main.split("@")[1]
        mail_account = None
        for line in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            a = _pml(line)
            if a and a["email"].split("@")[0].split("+")[0] + "@" + a["email"].split("@")[1] == base:
                mail_account = a
                break
        if mail_account is None:
            print("  号池找不到主号收码账号")
            return 2
        client = _bmc(mail_account, proxy=r.session_url or None,
                      impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
        print("  [OTP] 等待验证码...")
        otp = client.wait_for_otp(
            after_ts=otp_after,
            timeout=max(int(cfg.get("mail", {}).get("max_wait", 90)), 150),
            interval=3, settle_seconds=5,
        )
        print(f"  [OTP] {otp}")
        code_el = page.locator("input[name=code]").first
        if code_el.count():
            code_el.fill(otp)
            print("  [OTP] 填验证码")
            try:
                page.locator("button:has-text('Continue')").first.click()
            except Exception:
                page.keyboard.press("Enter")
        page.wait_for_timeout(6000)
        print(f"  登录后 URL: {page.url[:100]}")

        # 3.5 若停在 about-you(密码账号未补资料),尝试填 name/birthdate 提交
        if "about-you" in page.url or "about_you" in page.url:
            print("  [about-you] 密码账号需补资料,尝试填写")
            _dump(page, "about-you页")
            try:
                # 填 name
                name_el = page.locator("input[name=name]").first
                if name_el.count():
                    name_el.fill("James Miller")
                    print("  填 name: James Miller")
                # 填 age(从 birthdate 1998-05-12 算约 27)
                age_el = page.locator("input[name=age]").first
                if age_el.count():
                    age_el.fill("27")
                    print("  填 age: 27")
                # 点 Finish creating account
                btn = page.locator("button:has-text('Finish creating account')").first
                if btn.count():
                    btn.click()
                    print("  点 Finish creating account")
                else:
                    page.keyboard.press("Enter")
                page.wait_for_timeout(8000)
                print(f"  提交后 URL: {page.url[:100]}")
            except Exception as exc:
                print(f"  about-you 填写失败: {exc}")

        # 4. 进安全页看 TOTP
        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector("text=Security keys & passkeys", timeout=25000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        txt = page.evaluate("() => document.body.innerText")
        print(f"\n[安全页] URL={page.url}  body {len(txt)} 字符")
        print(f"  [正文] {txt[300:1200].replace(chr(10), ' | ')}")
        print("\n=== 找 TOTP/2FA/验证器 ===")
        for kw in ("TOTP", "two-factor", "authenticator", "Authenticator", "2FA",
                   "one-time code", "verification app", "App", "passkey", "Password"):
            idx = txt.find(kw)
            if idx >= 0:
                print(f"  含[{kw}] @{idx}: ...{txt[max(0,idx-25):idx+70]}...")
        # dump 按钮
        try:
            btns = page.eval_on_selector_all(
                "button", "els => els.map(e => (e.innerText||'').trim().slice(0,40)).filter(x=>x)")
            print("\n按钮:", btns[:25])
        except Exception:
            pass
        b.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

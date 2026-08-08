#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""侦查密码注册的正确 auth step:Python 走 OTP 拿 session cookies → Playwright 打开
create-account/password 页,监听它真实发出的请求,找到推进 password step 的 API。

用法: python capture/probe_password_flow.py --email <未注册邮箱> --proxy <代理>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config, resolve_path  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client, mail_identity_key, UsedCodeCache  # noqa: E402
from gptreg.register_otp import _root  # noqa: E402
from capture.verify_password_register import pick_free_account  # noqa: E402


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--proxy", default="")
    args = ap.parse_args()

    cfg = load_config()
    resolved = resolve_proxy(cfg, override=args.proxy or None)
    session = BrowserSession(cfg, proxy=resolved.session_url)
    print(f"代理: {resolved.label()}")

    account, email = pick_free_account(cfg, force_email=args.email)
    print(f"邮箱: {email}")

    # --- Python 走 signin + OTP validate,拿 session cookies ---
    auth.get_providers(session)
    time.sleep(0.3)
    csrf = auth.get_csrf_token(session)
    time.sleep(0.3)
    authorize_url = auth.signin_openai(session, csrf, email)
    otp_after = time.time()
    time.sleep(0.3)
    auth.follow_authorize(session, authorize_url)
    time.sleep(1.5)
    sentinel_otp, _ = auth.make_sentinel_headers(session, None, "authorize_continue", source="pow")
    mail_cfg = cfg.get("mail", {})
    browser_cfg = cfg.get("browser", {})
    client = build_mail_client(
        account,
        proxy=resolved.session_url or None,
        impersonate=browser_cfg.get("impersonate", "chrome142"),
    )
    identity = mail_identity_key(account)
    cache_path = resolve_path(mail_cfg.get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
    used_cache = UsedCodeCache(cache_path)
    exclude = used_cache.seen_codes(identity)
    print("等 OTP...")
    otp = client.wait_for_otp(
        after_ts=otp_after,
        timeout=max(int(mail_cfg.get("max_wait", 90)), 180),
        interval=int(mail_cfg.get("poll_interval", 3)),
        settle_seconds=int(mail_cfg.get("settle_seconds", 5)),
        exclude_codes=exclude,
    )
    used_cache.remember(identity, otp, email=email, status="submitted")
    print(f"OTP: {otp}")
    validate_result = auth.validate_email_otp(session, otp, sentinel_otp)
    print(f"validate 返回: {str(validate_result)[:250]}")

    # 导出 cookies(注意 .jar 才返回 Cookie 对象,直接迭代是 str)
    cookies = []
    for c in session.session.cookies.jar:
        cookies.append({
            "name": c.name, "value": c.value, "domain": c.domain,
            "path": c.path, "secure": bool(getattr(c, "secure", False)),
        })
    print(f"cookies: {len(cookies)} 个, 含 auth: {[c['name'] for c in cookies if 'auth' in c['name'].lower()]}")

    # --- Playwright 导入 cookies,打开 create-account/password,抓请求 ---
    from playwright.sync_api import sync_playwright

    url = "https://auth.openai.com/create-account/password"
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": resolved.session_url},
        )
        ctx = browser.new_context(
            user_agent=session.user_agent, locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )
        for c in cookies:
            try:
                ctx.add_cookies([{"name": c["name"], "value": c["value"],
                                  "domain": c["domain"].lstrip(".") or "openai.com",
                                  "path": c["path"] or "/"}])
            except Exception:
                pass
        page = ctx.new_page()

        captured = []
        def on_req(req):
            u = req.url
            if "openai.com" in u and ("authorize" in u or "password" in u or "register" in u
                                      or "step" in u or "otp" in u or "callback" in u):
                rec = {"method": req.method, "url": u[:160]}
                if req.method == "POST":
                    try:
                        rec["body"] = (req.post_data or "")[:200]
                    except Exception:
                        pass
                captured.append(rec)
                print(f"  REQ {req.method} {u[:160]}")
                if "body" in rec:
                    print(f"      body: {rec['body']}")
        page.on("request", on_req)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            print(f"  goto 异常: {exc}")
        page.wait_for_timeout(4000)
        try:
            print(f"  落点 URL: {page.url[:200]}")
        except Exception:
            pass

        # 尝试找密码输入框并提交
        try:
            pw_selectors = ["input[name=password]", "input[type=password]",
                            "#password", "[data-testid=password]"]
            box = None
            for s in pw_selectors:
                try:
                    box = page.locator(s).first
                    if box.count():
                        break
                except Exception:
                    box = None
            if box is not None and box.count():
                print("  [找到密码输入框] 填入密码并提交")
                box.fill("ProbePass12345!")
                try:
                    page.keyboard.press("Enter")
                except Exception:
                    try:
                        page.locator("button[type=submit]").first.click()
                    except Exception:
                        pass
                page.wait_for_timeout(4000)
            else:
                print("  [未找到密码输入框] 页面内容:")
                try:
                    print("   " + page.content()[:400].replace("\n", " "))
                except Exception:
                    pass
        except Exception as exc:
            print(f"  填密码异常: {exc}")

        print(f"\n=== 捕获 {len(captured)} 个请求 ===")
        for rec in captured:
            print(f"  {rec['method']} {rec['url']}")
        ctx.close()
        browser.close()
    resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

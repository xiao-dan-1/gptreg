#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""浏览器完整激活 TOTP 2FA: 纯协议密码登录拿新鲜会话 → cookies 注入浏览器 →
   安全页 → 点 Authenticator app → 抓 secret → pyotp confirm。

背景:
  - verify_pwd_totp 的 mfa/enroll 只拿 secret 没 confirm → 2FA 未真正激活
    (probe_totp_login 实证: password/verify 后直接给 code, 登录不要求 TOTP)
  - 本脚本走真实 UI 激活流程, 同时监听网络请求抓 enroll + confirm 的真实 API 结构

流程:
  Phase A(纯协议, 已通): signin_openai → authorize/continue → password/verify
      → follow callback(建 chatgpt 会话) → 导出 cookies(会话新鲜 = recent_auth 满足)
  Phase B(浏览器): 注入 cookies → settings/security → 点 Authenticator app →
      抓 secret(otpauth/base32/Trouble scanning) → pyotp 生成 6 位码 → 填 Step2 → Verify

用法: python capture/enable_totp_ui.py [--email 账号关键字] [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402
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


def _body(page) -> str:
    try:
        return page.evaluate("() => document.body.innerText")
    except Exception:
        return ""


def _phase_a_login(cfg, acc, email, password, proxy_url) -> list[dict] | None:
    """纯协议密码登录 → 新鲜会话 cookies。成功返回 playwright 可注入的 cookies 列表。"""
    resolved = resolve_proxy(cfg, override=proxy_url)
    s = BrowserSession(cfg, proxy=resolved.session_url)
    s.device_id = acc.get("device_id") or s.device_id
    print(f"  [A] 代理: {resolved.label()}  device_id={s.device_id[:8]}")

    def _api_headers(ref: str) -> dict:
        h = s.auth_api_headers(referer=ref)
        h.pop("content-type", None)
        h["content-type"] = "application/json"
        return h

    try:
        auth.get_providers(s)
        time.sleep(0.3)
        csrf = auth.get_csrf_token(s)
        time.sleep(0.3)
        au = auth.signin_openai(s, csrf, email)
        time.sleep(0.3)
        final = auth.follow_authorize(s, au, attempts=1)
        print(f"  [A] authorize 落点: {final[:70]}")
        time.sleep(0.3)

        # authorize/continue(邮箱)
        tok_ac, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="authorize_continue", cfg=cfg)
        h = _api_headers("https://auth.openai.com/log-in")
        h["openai-sentinel-token"] = tok_ac
        resp2 = s.post("https://auth.openai.com/api/accounts/authorize/continue",
                       headers=h, data=json.dumps({"username": {"kind": "email", "value": email}}),
                       allow_redirects=False, timeout=30)
        print(f"  [A] authorize/continue -> {resp2.status_code}")
        if resp2.status_code != 200:
            print(f"      body: {(resp2.text or '')[:200]}")
            return None

        # password/verify(密码)
        tok_pw, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="password_verify", cfg=cfg)
        h = _api_headers("https://auth.openai.com/log-in/password")
        h["openai-sentinel-token"] = tok_pw
        resp3 = s.post("https://auth.openai.com/api/accounts/password/verify",
                       headers=h, data=json.dumps({"password": password}),
                       allow_redirects=False, timeout=30)
        print(f"  [A] password/verify -> {resp3.status_code}")
        if resp3.status_code != 200:
            print(f"      body: {(resp3.text or '')[:200]}")
            return None
        c3 = resp3.json()
        cb = (c3.get("continue_url")
              or (c3.get("page") or {}).get("payload", {}).get("url") or "")
        if cb and "callback" in cb:
            auth.follow_oauth_callback(s, cb)
            print(f"  [A] follow callback 完成(建 chatgpt 会话)")
        else:
            print(f"  [A] 无 callback URL(continue_url={str(cb)[:60]})")

        cookies = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
             "secure": bool(getattr(c, "secure", False)),
             "expires": getattr(c, "expires", None)}
            for c in s.session.cookies.jar
        ]
        print(f"  [A] 会话 cookies: {len(cookies)} 个")
        return cookies
    finally:
        resolved.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="KathrynEverett6196")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    email = acc["email"]
    password = acc.get("password") or ""
    main_email = email.split("+")[0] + "@" + email.split("@")[1]
    print(f"账号: {email}  主号: {main_email}")

    # ---- Phase A: 纯协议密码登录(新鲜会话) ----
    print("\n[Phase A] 纯协议密码登录(新鲜 recent_auth)")
    cookies = _phase_a_login(cfg, acc, email, password, args.proxy)
    if not cookies:
        print("[x] Phase A 登录失败")
        return 2

    # ---- Phase B: 浏览器 UI 激活 ----
    print("\n[Phase B] 浏览器 UI 激活 TOTP")
    from playwright.sync_api import sync_playwright

    r = resolve_proxy(cfg, override=args.proxy)
    pp = urlparse(r.session_url)
    pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
    if pp.username:
        pw["username"] = unquote(pp.username)
        pw["password"] = unquote(pp.password or "")
    print(f"  代理: {r.label()}")

    mfa_log: list[dict] = []
    t0 = time.time()

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
        for c in cookies:
            for d in (c["domain"].lstrip("."), c["domain"]):
                if not d:
                    continue
                try:
                    ctx.add_cookies([{"name": c["name"], "value": c["value"], "domain": d,
                                      "path": c["path"], "secure": c["secure"]}])
                except Exception:
                    pass
        page = ctx.new_page()

        def _on_resp(resp):
            url = resp.url
            if ("/mfa" in url) or ("/totp" in url) or ("/accounts/security" in url):
                try:
                    body = resp.text()[:2000]
                except Exception:
                    body = ""
                mfa_log.append({"method": resp.request.method, "url": url,
                                "status": resp.status, "body": body})

        page.on("response", _on_resp)

        # B1. 打开安全页
        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        txt = _body(page)
        print(f"\n[B1] 安全页 URL={page.url[:70]} body={len(txt)}")
        if "auth.openai" in page.url or "log-in" in page.url:
            print("  [!] cookies 未建立 chatgpt 会话, 停在登录页")
            print(f"  body: {txt[:300]}")
            b.close(); r.close(); return 3

        # B2. 点 Authenticator app
        loc = page.locator("text=Authenticator app").first
        if not loc.count():
            loc = page.locator("text=authenticator").first
        if not loc.count():
            print("  [!] 找不到 Authenticator app 入口")
            for kw in ("two-factor", "2FA", "MFA", "multi-factor"):
                i = txt.find(kw)
                if i >= 0:
                    print(f"  {kw}@: {txt[max(0,i-20):i+80]}")
            b.close(); r.close(); return 4
        loc.click(force=True)
        print("[B2] 点击 Authenticator app")
        page.wait_for_timeout(4000)
        print(f"  点击后 URL: {page.url[:70]}")

        # B3. re-auth 处理(若 recent_auth 仍不足)
        for _ in range(6):
            if "auth.openai" in page.url and "chatgpt.com" not in page.url:
                print("  [B3] 需要 re-auth")
                cwp = page.locator("text=Continue with password").first
                if cwp.count():
                    cwp.click(force=True)
                    page.wait_for_timeout(3000)
                pw_el = page.locator("input[type=password], input[name=password]").first
                if pw_el.count():
                    pw_el.fill(password)
                    page.wait_for_timeout(500)
                    try:
                        page.locator("button:has-text('Continue')").first.click()
                    except Exception:
                        page.keyboard.press("Enter")
                    page.wait_for_timeout(5000)
                    print(f"  re-auth 填密码 → {page.url[:60]}")
                break
            page.wait_for_timeout(1500)
        if "chatgpt.com" not in page.url:
            page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            loc = page.locator("text=Authenticator app").first
            if loc.count():
                loc.click(force=True)
                page.wait_for_timeout(4000)
        print(f"  re-auth 后 URL: {page.url[:70]}")

        # B4. 等 TOTP 设置页
        print("\n[B4] 等 TOTP 设置页")
        txt = ""
        for _ in range(15):
            page.wait_for_timeout(1000)
            txt = _body(page)
            if ("scan the qr" in txt.lower() or "enter your 6-digit" in txt.lower()
                    or "authenticator app" in txt.lower()):
                break
        print(f"  URL={page.url[:70]} body={len(txt)}")

        # B5. 抓 secret
        print("\n[B5] 抓 TOTP secret")
        secret = None
        m_otp = re.search(r"otpauth://[^\s\"']+", txt)
        m_sec = re.search(r"[A-Z2-7]{32}", txt)
        if m_otp:
            secret = m_otp.group(0)
            m2 = re.search(r"[?&]secret=([A-Z2-7]+)", secret)
            if m2:
                secret = m2.group(1)
        elif m_sec:
            secret = m_sec.group(0)
        if not secret:
            try:
                ts = page.locator("text=Trouble scanning").first
                if ts.count():
                    ts.click()
                    page.wait_for_timeout(2500)
                    txt2 = _body(page)
                    m_sec2 = re.search(r"[A-Z2-7]{32}", txt2)
                    if m_sec2:
                        secret = m_sec2.group(0)
                        print("  Trouble scanning 抓取成功")
            except Exception as exc:
                print(f"  Trouble scanning 失败: {exc}")
        if not secret:
            print("  [!] 未抓到 secret")
            print(f"  body: {txt[200:1400]}")
            b.close(); r.close(); return 5
        print(f"  secret: {secret}")

        # B6. pyotp confirm
        print("\n[B6] pyotp 生成 6 位码并提交(confirm)")
        import pyotp
        totp = pyotp.TOTP(secret)
        code6 = totp.now()
        print(f"  6位码: {code6}")
        filled = False
        for _ in range(8):
            for sel in ("input[name=code]", "input[autocomplete=one-time-code]",
                        "input[type=tel]", "input[type=text]", "input"):
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
                    print("  点击 Verify")
                else:
                    page.keyboard.press("Enter")
            except Exception:
                page.keyboard.press("Enter")
            page.wait_for_timeout(6000)
            txt3 = _body(page)
            print(f"  Verify 后 URL: {page.url[:70]}")
            ok = any(k in txt3.lower() for k in ("enabled", "authenticator", "successful"))
            if ok:
                print("  [OK] TOTP 已启用确认")
            else:
                print("  [?] 提交后状态未知:")
                for kw in ("enabled", "error", "invalid", "wrong", "try again"):
                    i = txt3.lower().find(kw)
                    if i >= 0:
                        print(f"    [{kw}] {txt3[max(0,i-30):i+100]}")
        else:
            print("  [!] 未找到 Step2 验证码输入框")

        # B7. 输出
        print("\n" + "=" * 50)
        print(f"账号: {email}")
        print(f"密码: {password}")
        print(f"TOTP: {secret}")
        print(f"otpauth: otpauth://totp/ChatGPT:{email}?secret={secret}&issuer=ChatGPT")
        print("=" * 50)
        from gptreg.account_store import save_account
        save_account(cfg, record={
            "email": email, "password": password, "totp_secret": secret,
            "status": "ok", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        print("已保存到 accounts.jsonl(含 totp_secret)")

        print(f"\n=== 捕获的 mfa/totp 网络请求({len(mfa_log)}) ===")
        for m in mfa_log:
            print(f"  {m['method']} {m['status']} {m['url']}")
            if m.get("body"):
                print(f"    {m['body'][:800]}")
        print(f"\n[总耗时] {(time.time()-t0):.0f}s")
        b.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

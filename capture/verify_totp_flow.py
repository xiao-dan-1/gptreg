#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""浏览器完整登录(标准用户流程) → 提取 access_token → mfa/enroll 完整 dump + confirm 实验 → 复查。

背景:
  - mfa/enroll 需要 fresh recent_auth(纯协议 probe_enroll 得 401)
  - 密码账号 register 建号后 create_account 400 user_already_exists(常规), 拿 at 要走登录
  - 浏览器登录让浏览器自动处理 OAuth/PKCE/at, 再在页面上下文 fetch(带 at) 调 mfa 接口
  - 若 2FA 已激活, 登录时会要求 TOTP 码(用已存 secret pyotp 生成) → 顺带验证 TOTP 闭环

流程: chatgpt.com 首页 → Log in → email → password → (OTP 兜底) → 回 chatgpt.com →
  提取 at → fetch mfa_info → fetch mfa/enroll(完整 dump) → pyotp 码 confirm 实验 → 复查 mfa_info

用法: python capture/verify_totp_flow.py [--email 账号] [--password 密码] [--totp-secret 可选] [--proxy ...]
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
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402


def _body(page) -> str:
    try:
        return page.evaluate("() => document.body.innerText")
    except Exception:
        return ""


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="JenniferMitchell9500+8e13vo@outlook.com")
    ap.add_argument("--password", default="")
    ap.add_argument("--totp-secret", default="", help="若账号已有 TOTP secret, 登录要求 6 位码时用")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    main_email = args.email.split("+")[0] + "@" + args.email.split("@")[1]
    print(f"账号: {args.email}  主号: {main_email}")

    from playwright.sync_api import sync_playwright

    r = resolve_proxy(cfg, override=args.proxy)
    pp = urlparse(r.session_url)
    pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
    if pp.username:
        pw["username"] = unquote(pp.username)
        pw["password"] = unquote(pp.password or "")
    print(f"代理: {r.label()}")

    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy=pw,
        )
        ctx = b.new_context(user_agent=cfg.get("browser", {}).get("user_agent"),
                            locale="en-US", viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        mfa_log = []
        auth_headers: dict[str, str] = {}

        def _on_resp(resp):
            if "/mfa" in resp.url or "/totp" in resp.url or "/security" in resp.url:
                try:
                    mfa_log.append({"m": resp.request.method, "s": resp.status, "u": resp.url,
                                    "b": resp.text()[:1500]})
                except Exception:
                    pass

        def _on_req(req):
            if "/backend-api/" in req.url:
                h = req.headers.get("authorization", "")
                if "Bearer" in h and not auth_headers:
                    auth_headers[req.url] = h

        page.on("response", _on_resp)
        page.on("request", _on_req)

        # ---- 1. 登录 ----
        # Phase A: 纯协议登录(完整链: signin → authorize/continue → password/verify → [OTP] → callback)
        print("\n[1] Phase A 纯协议登录(含 OTP 分支)")
        from gptreg.session import BrowserSession as _BS
        from gptreg import auth as _A
        from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs as _GQ
        from gptreg.mail.providers import build_mail_client as _BMC

        resolvedA = resolve_proxy(cfg, override=args.proxy)
        sA = _BS(cfg, proxy=resolvedA.session_url)
        _A.get_providers(sA)
        time.sleep(0.3)
        _csrf = _A.get_csrf_token(sA)
        time.sleep(0.3)
        _au = _A.signin_openai(sA, _csrf, args.email)
        time.sleep(0.3)
        _A.follow_authorize(sA, _au, attempts=1)
        time.sleep(0.3)

        def _ah(ref: str) -> dict:
            h = sA.auth_api_headers(referer=ref)
            h.pop("content-type", None)
            h["content-type"] = "application/json"
            return h

        tok_ac, _ = _GQ(sA, sA.device_id, flow="authorize_continue", cfg=cfg)
        h = _ah("https://auth.openai.com/log-in")
        h["openai-sentinel-token"] = tok_ac
        r2 = sA.post("https://auth.openai.com/api/accounts/authorize/continue",
                     headers=h, data=json.dumps({"username": {"kind": "email", "value": args.email}}),
                     allow_redirects=False, timeout=30)
        print(f"  authorize/continue -> {r2.status_code}")
        if r2.status_code != 200:
            print(f"  body: {(r2.text or '')[:200]}")
            resolvedA.close(); b.close(); r.close(); return 2

        tok_pw, _ = _GQ(sA, sA.device_id, flow="password_verify", cfg=cfg)
        h = _ah("https://auth.openai.com/log-in/password")
        h["openai-sentinel-token"] = tok_pw
        r3 = sA.post("https://auth.openai.com/api/accounts/password/verify",
                     headers=h, data=json.dumps({"password": args.password}),
                     allow_redirects=False, timeout=30)
        print(f"  password/verify -> {r3.status_code}")
        if r3.status_code != 200:
            print(f"  body: {(r3.text or '')[:200]}")
            resolvedA.close(); b.close(); r.close(); return 2
        c3 = r3.json()
        cont = c3.get("continue_url") or ""
        page_type = (c3.get("page") or {}).get("type") or ""
        print(f"  continue_url: {cont[:70]}  page_type: {page_type}")

        # OTP 分支
        if page_type == "email_otp_verification" or "email-otp" in cont:
            print("  需要邮箱 OTP")
            mail_account = _find_mail_account(main_email)
            client = _BMC(mail_account, proxy=resolvedA.session_url or None,
                          impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
            otp = client.wait_for_otp(after_ts=time.time(), timeout=150, interval=3, settle_seconds=5)
            print(f"  OTP: {otp}")
            vr = _A.validate_email_otp(sA, otp, None)
            cont = vr.get("continue_url") or cont or ""
            page_type = (vr.get("page") or {}).get("type") or page_type
            print(f"  validate 后 continue_url: {cont[:70]}  page_type: {page_type}")

        # callback(建 chatgpt 会话)
        if cont and ("callback" in cont or "chatgpt.com" in cont):
            _A.follow_oauth_callback(sA, cont)
            print("  follow callback 完成")
        else:
            print(f"  [warn] 无 callback URL: {cont[:60]}")
        cookies = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
             "secure": bool(getattr(c, "secure", False))}
            for c in sA.session.cookies.jar
        ]
        print(f"  会话 cookies: {len(cookies)}")
        # resolvedA/sA 保留到最后关闭(Python requests 调 mfa 用, 避免 Cloudflare 拦截 page.evaluate fetch)

        for c in cookies:
            for d in (c["domain"].lstrip("."), c["domain"]):
                if d:
                    try:
                        ctx.add_cookies([{"name": c["name"], "value": c["value"], "domain": d,
                                          "path": c["path"], "secure": c["secure"]}])
                    except Exception:
                        pass
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        print(f"  登录态首页 URL: {page.url[:60]}")
        txt0 = _body(page)
        if "auth.openai" in page.url or "log in" in txt0.lower()[:600]:
            print("  [!] 登录态未建立")
            print(f"  body: {txt0[:200]}")
            b.close(); r.close(); return 2
        # ---- 2. 提取 at ----
        print("\n[2] 提取 access_token")
        # 导航到安全页: 前端必发 /backend-api/accounts/mfa_info + security_settings/info(带 Bearer)
        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(8000)
        at = ""
        if auth_headers:
            first_url = next(iter(auth_headers))
            at = auth_headers[first_url].replace("Bearer ", "", 1).strip()
            print(f"  从前端请求头抓取: {first_url[:70]}")
            print(f"  at 前40: {at[:40]}")
        else:
            print("  [!] 未抓到 Authorization 头")
            # 兜底1: fetch /api/auth/session 看是否含 accessToken
            try:
                sess = page.evaluate("""async () => {
                    const r = await fetch('/api/auth/session');
                    return {status: r.status, text: await r.text()};
                }""")
                print(f"  /api/auth/session -> {sess['status']}: {sess['text'][:600]}")
            except Exception as exc:
                print(f"  /api/auth/session 异常: {str(exc)[:80]}")
            # 兜底2: dump localStorage keys
            try:
                ls = page.evaluate("() => { const o={}; for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i); o[k]=(localStorage.getItem(k)||'').slice(0,60);} return o; }")
                print(f"  localStorage: {json.dumps(ls, ensure_ascii=False)[:700]}")
            except Exception:
                pass
            b.close(); r.close(); return 3
        if not at.startswith("eyJ"):
            print("  [!] at 不是 JWT 格式, 继续但可能失败")

        # ---- 3. mfa 接口实验(Python requests + at, 避免 Cloudflare 拦 page.evaluate fetch) ----
        print("\n[3] mfa 接口实验")
        h = sA.chatgpt_headers(referer="https://chatgpt.com/settings/security")
        h["authorization"] = f"Bearer {at}"
        h["oai-device-id"] = sA.device_id
        h.pop("content-type", None)
        h["content-type"] = "application/json"
        mfa_base = "https://chatgpt.com/backend-api/accounts/mfa"

        def _mfa(method: str, path: str, body=None):
            if path == "info":
                url = "https://chatgpt.com/backend-api/accounts/mfa_info"
            else:
                url = f"{mfa_base}/{path}"
            if method == "GET":
                rq = sA.get(url, headers=h, timeout=30)
            else:
                rq = sA.post(url, headers=h,
                             data=json.dumps(body) if body else None, timeout=30)
            return {"status": rq.status_code, "text": rq.text}

        info = _mfa("GET", "info")
        print(f"  [mfa_info] {info['status']}: {info['text'][:600]}")

        enroll = _mfa("POST", "enroll", {"factor_type": "totp"})
        print(f"\n  [enroll] {enroll['status']}")
        print(f"  完整响应: {enroll['text'][:2500]}")
        if enroll["status"] != 200:
            print("  [x] enroll 失败, 停止")
            b.close(); r.close(); return 4

        secret = None
        body = enroll["text"]
        factor_id = None
        session_id = None
        try:
            erj = json.loads(body)
            factor_id = (erj.get("factor") or {}).get("id")
            session_id = erj.get("session_id")
            print(f"  factor_id: {factor_id}")
            print(f"  session_id: {session_id}")
        except Exception:
            pass
        m_otp = re.search(r"otpauth://[^\s\"']+", body)
        m_sec = re.search(r"[A-Z2-7]{32}", body)
        if m_otp:
            secret = m_otp.group(0)
            m2 = re.search(r"[?&]secret=([A-Z2-7]+)", secret)
            if m2:
                secret = m2.group(1)
        elif m_sec:
            secret = m_sec.group(0)
        print(f"  secret: {secret}")
        if not secret:
            b.close(); r.close(); return 5

        # ---- 4. confirm 实验 ----
        import pyotp
        code6 = pyotp.TOTP(secret).now()
        print(f"\n[4] pyotp 6位码: {code6} → confirm 候选实验")
        candidates = [
            ("POST", "user/activate_enrollment", {"code": code6, "session_id": session_id, "factor_id": factor_id, "factor_type": "totp"}),
            ("POST", "user/activate_enrollment", {"code": code6, "factor_id": factor_id, "factor_type": "totp"}),
            ("POST", "user/activate_enrollment", {"code": code6, "session_id": session_id, "factor_type": "totp"}),
            ("POST", "user/activate_enrollment", {"code": code6, "factor": {"id": factor_id, "factor_type": "totp"}}),
            ("POST", "user/activate_enrollment", {"code": code6, "session_id": session_id, "factor_id": factor_id}),
            ("POST", "totp/confirm", {"code": code6, "session_id": session_id, "factor_id": factor_id}),
        ]
        for method, path, payload in candidates:
            rc = _mfa(method, path, payload)
            print(f"  {method} {path} {list(payload.keys())} -> {rc['status']}: {rc['text'][:220]}")
            time.sleep(0.4)

        # ---- 5. 复查 ----
        print("\n[5] 复查 mfa_info")
        info2 = _mfa("GET", "info")
        print(f"  {info2['status']}: {info2['text'][:600]}")
        enabled = '"mfa_enabled":true' in info2["text"] or '"mfa_enabled": true' in info2["text"]
        print(f"\n  >>> mfa_enabled: {enabled}")
        if enabled:
            print("  [OK] TOTP 2FA 已真正激活!")
        else:
            print("  [x] 仍未激活, confirm endpoint 未命中")

        # ---- 5.5 UI 回退: 点 Authenticator app 走真实流程, 抓真实 enroll/confirm URL ----
        if not enabled:
            print("\n[5.5] UI 回退: 点 Authenticator app 抓真实 confirm URL")
            page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            click_sel = None
            for sel in ("[data-testid=mfa-authenticator-toggle]", "[data-testid=mfa-authenticator]",
                        "text=Authenticator app"):
                try:
                    loc = page.locator(sel).first
                    if loc.count():
                        if sel.startswith("["):
                            page.evaluate("""(s) => {
                                const el = document.querySelector(s);
                                if (el) for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                                    el.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window}));
                                }
                            }""", sel)
                        else:
                            loc.click(force=True)
                        print(f"  点击 {sel}")
                        click_sel = sel
                        break
                except Exception as exc:
                    print(f"  {sel} 点击异常: {str(exc)[:60]}")
            if click_sel:
                page.wait_for_timeout(4000)
                # re-auth 处理
                for _ in range(5):
                    if "auth.openai" in page.url and "chatgpt.com" not in page.url:
                        cwp = page.locator("text=Continue with password").first
                        if cwp.count():
                            cwp.click(force=True)
                            page.wait_for_timeout(3000)
                        pw_el = page.locator("input[type=password]").first
                        if pw_el.count():
                            pw_el.fill(args.password)
                            page.wait_for_timeout(500)
                            try:
                                page.locator("button:has-text('Continue')").first.click()
                            except Exception:
                                page.keyboard.press("Enter")
                            page.wait_for_timeout(5000)
                        break
                    page.wait_for_timeout(1500)
                if "chatgpt.com" not in page.url:
                    page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(5000)
            # 等 TOTP 设置文本
            txt2 = ""
            for _ in range(15):
                page.wait_for_timeout(1000)
                txt2 = _body(page)
                if "scan the qr" in txt2.lower() or "enter your 6-digit" in txt2.lower():
                    break
            print(f"  TOTP 设置 body={len(txt2)} URL={page.url[:60]}")
            # 抓 secret
            secret2 = None
            m_otp2 = re.search(r"otpauth://[^\s\"']+", txt2)
            m_sec2 = re.search(r"[A-Z2-7]{32}", txt2)
            if m_otp2:
                secret2 = m_otp2.group(0)
                m3 = re.search(r"[?&]secret=([A-Z2-7]+)", secret2)
                if m3:
                    secret2 = m3.group(1)
            elif m_sec2:
                secret2 = m_sec2.group(0)
            if not secret2:
                try:
                    ts = page.locator("text=Trouble scanning").first
                    if ts.count():
                        ts.click()
                        page.wait_for_timeout(2500)
                        txt3 = _body(page)
                        m_sec3 = re.search(r"[A-Z2-7]{32}", txt3)
                        if m_sec3:
                            secret2 = m_sec3.group(0)
                except Exception:
                    pass
            if secret2:
                print(f"  UI secret: {secret2}")
                code2 = pyotp.TOTP(secret2).now()
                print(f"  UI 6位码: {code2}")
                filled2 = False
                for _ in range(6):
                    for sel in ("input[name=code]", "input[autocomplete=one-time-code]", "input[type=text]", "input"):
                        try:
                            el = page.locator(sel).first
                            if el.count():
                                el.fill(code2)
                                filled2 = True
                                break
                        except Exception:
                            pass
                    if filled2:
                        break
                    page.wait_for_timeout(1000)
                if filled2:
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
                # 复查
                info3 = _mfa("GET", "info")
                print(f"  [复查] mfa_info: {info3['text'][:400]}")
                enabled = '"mfa_enabled":true' in info3["text"] or '"mfa_enabled": true' in info3["text"]
                print(f"  >>> UI 后 mfa_enabled: {enabled}")
                if enabled:
                    secret = secret2
            else:
                print("  [!] UI 未进入 TOTP 设置/未抓到 secret")

        # 输出
        print("\n" + "=" * 50)
        print(f"账号: {args.email}")
        print(f"密码: {args.password}")
        print(f"TOTP: {secret}")
        print(f"otpauth: otpauth://totp/ChatGPT:{args.email}?secret={secret}&issuer=ChatGPT")
        print("=" * 50)
        out = ROOT / "output" / "totp_accounts.txt"
        with out.open("a", encoding="utf-8") as f:
            f.write(f"{args.email}----{args.password}----{secret}\n")

        print(f"\n=== 捕获的 mfa 网络请求({len(mfa_log)}) ===")
        for m in mfa_log:
            print(f"  {m['m']} {m['s']} {m['u']}")
            if m["b"]:
                print(f"    {m['b'][:400]}")
        print(f"\n[总耗时] {(time.time()-t0):.0f}s")
        b.close()
    try:
        resolvedA.close()
    except Exception:
        pass
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""纯 API 全自动开启 TOTP 2FA(绕开 UI,最稳定)。

流程(学自 codex-register 纯协议密码登录):
  1. GET /oauth/authorize → 拿 login_session cookie
  2. POST /api/accounts/authorize/continue {username: email} + sentinel(authorize_continue)
  3. POST /api/accounts/password/verify {password} + sentinel(password_verify) → recent_auth
  4. 处理响应(可能 email_otp,需收码)
  5. OAuth 回调 → code → POST /oauth/token → access_token(带 recent_auth)
  6. POST /backend-api/accounts/mfa/enroll {"factor_type": "totp"} → TOTP secret
  7. pyotp 生成码 → 提交确认
  8. 输出 账号----密码----TOTP secret

用法: python capture/enable_totp_api.py [--email 密码账号] [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"
SCOPE = "openid email profile offline_access model.request model.read organization.read organization.write"


def _find_account(email_contains: str = "ThomasRivers7260") -> dict:
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


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="ThomasRivers7260")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    email = acc["email"]
    password = acc.get("password") or ""
    main_email = email.split("+")[0] + "@" + email.split("@")[1]
    print(f"账号: {email}  密码: {password[:4]}***  主号: {main_email}")

    r = resolve_proxy(cfg, override=args.proxy)
    s = BrowserSession(cfg, proxy=r.session_url)
    s.device_id = acc.get("device_id") or s.device_id
    print(f"代理: {r.label()}")

    # 1. bootstrap authorize(拿 login_session)
    code_verifier, code_challenge = _pkce()
    state = secrets.token_urlsafe(24)
    ap = {
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "scope": SCOPE, "code_challenge": code_challenge, "code_challenge_method": "S256",
        "state": state,
    }
    authorize_url = f"{ISSUER}/oauth/authorize?" + urlencode(ap)
    print("[1] GET /oauth/authorize")
    resp = s.get(authorize_url, headers=s.auth_navigate_headers(referer="https://chatgpt.com/"),
                 allow_redirects=True, timeout=30)
    has_login = any(c.name == "login_session" for c in s.session.cookies.jar)
    print(f"  -> {resp.status_code} login_session={has_login} final={str(getattr(resp,'url',''))[:60]}")

    # 2. authorize/continue(提交邮箱)
    def _api_headers(referer: str) -> dict:
        h = s.auth_api_headers(referer=referer)
        h.pop("content-type", None)
        h["content-type"] = "application/json"
        return h

    print("[2] POST authorize/continue(邮箱)")
    tok_ac, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="authorize_continue", cfg=cfg)
    h = _api_headers(f"{ISSUER}/log-in")
    h["openai-sentinel-token"] = tok_ac
    resp2 = s.post(f"{ISSUER}/api/accounts/authorize/continue",
                   headers=h, data=json.dumps({"username": {"kind": "email", "value": email}}),
                   allow_redirects=False, timeout=30)
    print(f"  -> {resp2.status_code}: {(resp2.text or '')[:200]}")
    if resp2.status_code != 200:
        print("  [x] authorize/continue 失败")
        return 2
    c2 = resp2.json()
    continue_url = c2.get("continue_url", "")
    page_type = (c2.get("page") or {}).get("type", "")

    # 3. password/verify(密码,recent_auth)
    print("[3] POST password/verify(密码)")
    tok_pw, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="password_verify", cfg=cfg)
    h = _api_headers(f"{ISSUER}/log-in/password")
    h["openai-sentinel-token"] = tok_pw
    resp3 = s.post(f"{ISSUER}/api/accounts/password/verify",
                   headers=h, data=json.dumps({"password": password}),
                   allow_redirects=False, timeout=30)
    print(f"  -> {resp3.status_code}: {(resp3.text or '')[:200]}")
    if resp3.status_code != 200:
        print("  [x] password/verify 失败")
        return 3
    c3 = resp3.json()
    continue_url = c3.get("continue_url", "") or continue_url
    page_type = (c3.get("page") or {}).get("type", "") or page_type
    print(f"  密码验证成功! continue_url={continue_url[:70]} page_type={page_type}")

    # 4. 处理 OTP 阶段
    if page_type == "email_otp_verification" or "email-otp" in (continue_url or ""):
        print("[4] 需要邮箱 OTP")
        otp_after = time.time()
        mail_account = _find_mail_account(main_email)
        client = build_mail_client(mail_account, proxy=r.session_url or None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
        otp = client.wait_for_otp(after_ts=otp_after,
                                  timeout=max(int(cfg.get("mail", {}).get("max_wait", 90)), 150),
                                  interval=3, settle_seconds=5)
        print(f"  OTP: {otp}")
        resp_otp = s.post(f"{ISSUER}/api/accounts/email-otp/validate",
                          headers=_api_headers(f"{ISSUER}/email-verification"),
                          data=json.dumps({"code": otp}), allow_redirects=False, timeout=30)
        print(f"  email-otp/validate -> {resp_otp.status_code}: {(resp_otp.text or '')[:150]}")
        if resp_otp.status_code == 200:
            c_otp = resp_otp.json()
            continue_url = c_otp.get("continue_url", "") or continue_url

    # 5. OAuth2 授权流程:跟随重定向链拿 code(allow_redirects=False,处理 consent)
    print(f"[5] OAuth2 授权: {continue_url[:60]}")
    from urllib.parse import urljoin as _uj

    nav_headers = s.auth_navigate_headers(referer=f"{ISSUER}/log-in/password")
    url = continue_url
    code = None
    for hop in range(8):
        resp = s.get(url, headers=nav_headers, allow_redirects=False, timeout=30)
        loc = resp.headers.get("location", "")
        print(f"  hop{hop} {resp.status_code} loc={loc[:80]}")
        if "code=" in loc:
            m = re.search(r"[?&]code=([^&]+)", loc)
            code = m.group(1)
            print(f"  拿到 code: {code[:40]}")
            break
        if "consent" in loc:
            print("  [consent] GET 跟随授权")
            resp_cc = s.get(loc, headers=nav_headers, allow_redirects=True, timeout=30)
            final2 = str(getattr(resp_cc, "url", ""))
            print(f"  consent -> {resp_cc.status_code} final={final2[:90]}")
            m2 = re.search(r"[?&]code=([^&]+)", final2)
            if m2:
                code = m2.group(1)
                print(f"  拿到 code: {code[:40]}")
                break
            # 若 consent 返回页面含 code 或跳转,解析
            m_cc = re.search(r"consent_challenge=([^&]+)", loc)
            if m_cc:
                resp_cc2 = s.post(f"{ISSUER}/api/accounts/consent",
                                  headers=_api_headers(loc[:80]),
                                  data=json.dumps({"consent_challenge": m_cc.group(1),
                                                    "scopes": "openid profile email offline_access"}),
                                  allow_redirects=False, timeout=30)
                loc3 = resp_cc2.headers.get("location", "")
                print(f"  consent POST -> {resp_cc2.status_code} loc={loc3[:80]}")
                m3 = re.search(r"[?&]code=([^&]+)", loc3)
                if m3:
                    code = m3.group(1)
                    print(f"  拿到 code: {code[:40]}")
                    break
                if loc3:
                    url = loc3 if loc3.startswith("http") else _uj(loc, loc3)
                    continue
            if final2 and "consent" not in final2:
                url = final2
                continue
        if not loc:
            # 可能是最终响应(HTML 或 code 在 URL)
            final_url = str(getattr(resp, "url", ""))
            m3 = re.search(r"[?&]code=([^&]+)", final_url)
            if m3:
                code = m3.group(1)
                print(f"  最终 URL code: {code[:40]}")
            break
        url = loc if loc.startswith("http") else _uj(url, loc)
    if not code:
        print("  [warn] 未拿到 code")
        return 5

    # 5.5 POST /oauth/token(code 交换, chatgpt client)
    print("[5.5] POST /oauth/token")
    resp_tok = s.post(f"{ISSUER}/oauth/token",
                      headers=_api_headers(f"{ISSUER}/"),
                      data=json.dumps({
                          "grant_type": "authorization_code", "code": code,
                          "redirect_uri": REDIRECT_URI, "client_id": CLIENT_ID,
                          "code_verifier": code_verifier,
                      }), allow_redirects=False, timeout=30)
    print(f"  -> {resp_tok.status_code}: {(resp_tok.text or '')[:200]}")
    if resp_tok.status_code != 200:
        print("  [x] token 交换失败")
        return 6
    tj = resp_tok.json()
    access_token = tj.get("access_token", "") or acc.get("access_token") or ""
    print(f"  access_token: {access_token[:30]}...")

    # 6. mfa/enroll
    print("[6] POST mfa/enroll(factor_type=totp)")
    h6 = s.chatgpt_headers(referer="https://chatgpt.com/")
    h6["authorization"] = f"Bearer {access_token}"
    h6["oai-device-id"] = s.device_id
    h6.pop("content-type", None)
    h6["content-type"] = "application/json"
    resp_enroll = s.post("https://chatgpt.com/backend-api/accounts/mfa/enroll",
                         headers=h6, data=json.dumps({"factor_type": "totp"}), timeout=30)
    print(f"  -> {resp_enroll.status_code}: {(resp_enroll.text or '')[:400]}")
    if resp_enroll.status_code != 200:
        print("  [x] enroll 失败(可能仍 recent_auth_required)")
        return 7
    erj = resp_enroll.json()
    secret = None
    # 从响应提取 secret/otpauth
    txt = resp_enroll.text
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
        # 看响应结构
        print(f"  [warn] 未提取到 secret, 响应: {json.dumps(erj, ensure_ascii=False)[:300]}")
        return 8
    print(f"\n🎉 TOTP secret: {secret}")
    print(f"账号: {email}")
    print(f"密码: {password}")
    print(f"TOTP: {secret}")
    from gptreg.account_store import save_account
    save_account(cfg, record={
        "email": email, "password": password, "totp_secret": secret,
        "status": "ok", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    print("已保存到 accounts.jsonl(含 totp_secret)")
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

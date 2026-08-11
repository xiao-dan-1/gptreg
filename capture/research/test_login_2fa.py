#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试纯协议产出的 password+TOTP 账号能否正常登录。

流程: authorize → authorize/continue → password/verify → TOTP 挑战 → chatgpt 会话
→ access_token → accounts/check（验证账号存活且登录成功）

用法: python capture/research/test_login_2fa.py [email 子串] [--proxy URL]
"""
from __future__ import annotations

import json
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin as _uj

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import pyotp  # noqa: E402
from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.health import check_account_health  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"
SCOPE = "openid email profile offline_access model.request model.read organization.read organization.write"


def _find_account(sub: str) -> dict:
    for line in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if sub in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {sub}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sub = args[0] if args else "9fbb16"
    proxy_override = None
    if "--proxy" in sys.argv:
        proxy_override = sys.argv[sys.argv.index("--proxy") + 1]

    cfg = load_config()
    r = resolve_proxy(cfg, override=proxy_override)
    acc = _find_account(sub)
    email = acc["email"]
    password = acc.get("password") or ""
    secret = acc.get("totp_secret") or ""
    print(f"账号: {email}  密码: {password[:4]}***  TOTP: {secret[:6]}...")
    print(f"代理: {r.label()}")

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id

    # 1. authorize → login_session
    state = secrets.token_urlsafe(24)
    ap = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
          "scope": SCOPE, "state": state}
    authz_url = f"{ISSUER}/oauth/authorize?" + urlencode(ap)
    print("\n[1] GET /oauth/authorize")
    sess.get(authz_url, headers=sess.auth_navigate_headers(referer="https://chatgpt.com/"),
             allow_redirects=True, timeout=30)
    has_login = any(c.name == "login_session" for c in sess.session.cookies.jar)
    print(f"  login_session={has_login}")

    def _api_headers(referer):
        h = sess.auth_api_headers(referer=referer)
        h.pop("content-type", None)
        h["content-type"] = "application/json"
        return h

    # 2. authorize/continue 提交邮箱
    print("[2] POST authorize/continue(邮箱)")
    tok_ac, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
    h = _api_headers(f"{ISSUER}/log-in")
    h["openai-sentinel-token"] = tok_ac
    resp2 = sess.post(f"{ISSUER}/api/accounts/authorize/continue",
                      headers=h, data=json.dumps({"username": {"kind": "email", "value": email}}),
                      allow_redirects=False, timeout=30)
    print(f"  -> {resp2.status_code}: {(resp2.text or '')[:120]}")
    if resp2.status_code != 200:
        print("  [x] authorize/continue 失败")
        return 1
    c2 = resp2.json()
    page_type = (c2.get("page") or {}).get("type", "")
    continue_url = c2.get("continue_url", "")
    print(f"  page_type={page_type}")

    # 3. password/verify（账号有密码）
    print("[3] POST password/verify")
    tok_pw, _ = get_sentinel_token_via_quickjs(sess, sess.device_id, flow="password_verify", cfg=cfg)
    h3 = _api_headers(f"{ISSUER}/log-in/password")
    h3["openai-sentinel-token"] = tok_pw
    resp3 = sess.post(f"{ISSUER}/api/accounts/password/verify",
                      headers=h3, data=json.dumps({"password": password}),
                      allow_redirects=False, timeout=30)
    print(f"  -> {resp3.status_code}: {(resp3.text or '')[:200]}")
    if resp3.status_code != 200:
        print("  [x] 密码验证失败")
        return 2
    c3 = resp3.json()
    page_type = (c3.get("page") or {}).get("type", "") or page_type
    continue_url = c3.get("continue_url", "") or continue_url
    print(f"  page_type={page_type}")

    # 4. 若 TOTP 挑战
    if page_type in ("mfa_challenge",) or "mfa" in (continue_url or ""):
        print("[4] TOTP 挑战")
        code = pyotp.TOTP(secret).now()
        factor_id = None
        try:
            factor_id = (c3.get("page") or {}).get("payload", {}).get("factor_id")
        except Exception:
            pass
        print(f"  TOTP code: {code}  factor_id: {factor_id}")
        # mfa/verify 需 type + id + code
        for payload in ({"type": "totp", "id": factor_id, "code": code},
                        {"type": "totp", "code": code}):
            h4 = _api_headers(f"{ISSUER}/log-in/password")
            resp4 = sess.post(f"{ISSUER}/api/accounts/mfa/verify",
                              headers=h4, data=json.dumps(payload),
                              allow_redirects=False, timeout=30)
            print(f"  mfa/verify {list(payload.keys())} -> {resp4.status_code}: {(resp4.text or '')[:150]}")
            if resp4.status_code == 200:
                c4 = resp4.json()
                continue_url = c4.get("continue_url", "") or continue_url
                page_type = (c4.get("page") or {}).get("type", "") or page_type
                print(f"  after TOTP: page_type={page_type}")
                break

    # 5. 完成 OAuth 回调 → chatgpt 会话
    print(f"[5] 完成登录: {continue_url[:60]}")
    if continue_url:
        try:
            cb = sess.get(continue_url, headers=sess.chatgpt_headers(), allow_redirects=True, timeout=30)
            print(f"  callback -> {cb.status_code}")
        except Exception as e:
            print(f"  callback 异常: {str(e)[:50]}")

    # 6. 拿 access_token
    at = ""
    for attempt in range(5):
        try:
            sr = sess.get("https://chatgpt.com/api/auth/session", headers=sess.chatgpt_headers(), timeout=20)
            if sr.status_code == 200:
                sj = sr.json()
                if sj and sj.get("accessToken"):
                    at = sj["accessToken"]
                    print(f"  新 access_token: {at[:25]}...")
                    break
                print(f"  session 无 token: {str(sj)[:80]}")
        except Exception as e:
            print(f"  session 异常: {str(e)[:50]}")
        time.sleep(2)

    # 7. 健康检查
    if at:
        print("[6] accounts/check")
        hc = check_account_health(sess, at)
        print(f"  -> status={hc.get('status')} http={hc.get('http')}")
        print(f"\n{'✅ 登录成功，账号存活' if hc.get('status') == 'ok' else '❌ 健康检查失败'}")
        return 0 if hc.get("status") == "ok" else 3
    print("  [x] 未拿到 access_token")
    r.close()
    return 4


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""纯协议账号（password + TOTP 2FA）完整登录：验证 → PKCE OAuth → 新 token → 健康检查。

流程（对齐 enable_totp_api.py + probe_totp_login2.py）:
  authorize(PKCE) → authorize/continue(邮箱) → password/verify → mfa/verify(TOTP)
  → OAuth 授权链 → /oauth/token → access_token → accounts/check

用法: python capture/research/login_2fa_pkce.py [email 子串] [--proxy URL]
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
from urllib.parse import urlencode, urlparse, parse_qs, urljoin as _uj

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


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


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

    # 1. authorize (PKCE)
    code_verifier, code_challenge = _pkce()
    state = secrets.token_urlsafe(24)
    ap = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
          "scope": SCOPE, "code_challenge": code_challenge, "code_challenge_method": "S256", "state": state}
    print("\n[1] GET /oauth/authorize (PKCE)")
    sess.get(f"{ISSUER}/oauth/authorize?" + urlencode(ap),
             headers=sess.auth_navigate_headers(referer="https://chatgpt.com/"),
             allow_redirects=True, timeout=30)

    def _api_headers(referer):
        h = sess.auth_api_headers(referer=referer)
        h.pop("content-type", None)
        h["content-type"] = "application/json"
        return h

    # 2. authorize/continue
    print("[2] POST authorize/continue(邮箱)")
    tok_ac, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
    h = _api_headers(f"{ISSUER}/log-in")
    h["openai-sentinel-token"] = tok_ac
    resp2 = sess.post(f"{ISSUER}/api/accounts/authorize/continue",
                      headers=h, data=json.dumps({"username": {"kind": "email", "value": email}}),
                      allow_redirects=False, timeout=30)
    if resp2.status_code != 200:
        print(f"  [x] authorize/continue 失败: {(resp2.text or '')[:150]}")
        return 1
    continue_url = resp2.json().get("continue_url", "")

    # 3. password/verify
    print("[3] POST password/verify")
    tok_pw, _ = get_sentinel_token_via_quickjs(sess, sess.device_id, flow="password_verify", cfg=cfg)
    h3 = _api_headers(f"{ISSUER}/log-in/password")
    h3["openai-sentinel-token"] = tok_pw
    resp3 = sess.post(f"{ISSUER}/api/accounts/password/verify",
                      headers=h3, data=json.dumps({"password": password}),
                      allow_redirects=False, timeout=30)
    if resp3.status_code != 200:
        print(f"  [x] 密码验证失败: {(resp3.text or '')[:150]}")
        return 2
    c3 = resp3.json()
    page_type = (c3.get("page") or {}).get("type", "")
    continue_url = c3.get("continue_url", "") or continue_url
    factor_id = None
    try:
        factor_id = (c3.get("page") or {}).get("payload", {}).get("factor_id")
    except Exception:
        pass
    print(f"  -> 200, page_type={page_type}")

    # 4. TOTP 挑战
    if "mfa" in page_type.lower() or "mfa" in (continue_url or ""):
        print("[4] POST mfa/verify (TOTP)")
        code = pyotp.TOTP(secret).now()
        h4 = _api_headers(f"{ISSUER}/log-in/password")
        resp4 = sess.post(f"{ISSUER}/api/accounts/mfa/verify",
                          headers=h4, data=json.dumps({"type": "totp", "id": factor_id, "code": code}),
                          allow_redirects=False, timeout=30)
        print(f"  -> {resp4.status_code}: {(resp4.text or '')[:120]}")
        if resp4.status_code != 200:
            print("  [x] TOTP 验证失败")
            return 3
        c4 = resp4.json()
        continue_url = c4.get("continue_url", "") or continue_url
        page_type = (c4.get("page") or {}).get("type", "") or page_type
        print(f"  -> TOTP 通过, page_type={page_type}")

    # 5. OAuth 授权链 → code
    print(f"[5] OAuth 授权: {continue_url[:60]}")
    code = None
    nav = sess.auth_navigate_headers(referer=f"{ISSUER}/log-in/password")
    url = continue_url
    for hop in range(8):
        resp = sess.get(url, headers=nav, allow_redirects=False, timeout=30)
        loc = resp.headers.get("location", "")
        m = re.search(r"[?&]code=([^&]+)", loc or "")
        if m:
            code = m.group(1)
            print(f"  hop{hop} 拿到 code")
            break
        if "consent" in (loc or ""):
            print(f"  hop{hop} consent 页, 提交 consent")
            m_cc = re.search(r"consent_challenge=([^&]+)", loc)
            if m_cc:
                resp_cc = sess.post(f"{ISSUER}/api/accounts/consent",
                                    headers=_api_headers(loc[:80]),
                                    data=json.dumps({"consent_challenge": m_cc.group(1),
                                                      "scopes": "openid profile email offline_access"}),
                                    allow_redirects=False, timeout=30)
                loc3 = resp_cc.headers.get("location", "")
                print(f"    consent POST -> {resp_cc.status_code} loc={loc3[:70]}")
                m3 = re.search(r"[?&]code=([^&]+)", loc3 or "")
                if m3:
                    code = m3.group(1)
                    print(f"    consent -> code")
                    break
                if loc3:
                    url = loc3 if loc3.startswith("http") else _uj(url, loc3)
                    continue
            resp_cc = sess.get(loc, headers=nav, allow_redirects=True, timeout=30)
            m2 = re.search(r"[?&]code=([^&]+)", str(getattr(resp_cc, "url", "")))
            if m2:
                code = m2.group(1)
                print(f"  consent -> code")
                break
        if not loc:
            m3 = re.search(r"[?&]code=([^&]+)", str(getattr(resp, "url", "")))
            if m3:
                code = m3.group(1)
                print(f"  hop{hop} 最终 URL code")
            break
        url = loc if loc.startswith("http") else _uj(url, loc)
    if not code:
        print("  [x] 未拿到 code")
        return 4

    # 6. /oauth/token 换 access_token
    print("[6] POST /oauth/token")
    h6 = _api_headers(f"{ISSUER}/")
    resp_tok = sess.post(f"{ISSUER}/oauth/token",
                         headers=h6, data=json.dumps({
                             "grant_type": "authorization_code", "code": code,
                             "redirect_uri": REDIRECT_URI, "client_id": CLIENT_ID,
                             "code_verifier": code_verifier}), allow_redirects=False, timeout=30)
    print(f"  -> {resp_tok.status_code}")
    if resp_tok.status_code != 200:
        print(f"  [x] token 交换失败: {(resp_tok.text or '')[:150]}")
        return 5
    at = resp_tok.json().get("access_token", "")
    print(f"  access_token: {at[:30]}...")

    # 7. 健康检查
    if at:
        print("[7] accounts/check")
        hc = check_account_health(sess, at)
        st = hc.get("status")
        print(f"  -> status={st} http={hc.get('http')}")
        if st == "ok":
            print(f"\n✅ 完整登录成功！纯协议账号（password+2fa）可用，新 token 已获取")
            # 更新账号记录的新 token
            acc2 = dict(acc)
            acc2["access_token"] = at
            acc2["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            from gptreg.account_store import save_account
            save_account(cfg, record=acc2)
            print(f"  已更新账号 access_token")
            return 0
        print("  [x] 健康检查失败")
        return 6
    r.close()
    return 7


if __name__ == "__main__":
    raise SystemExit(main())

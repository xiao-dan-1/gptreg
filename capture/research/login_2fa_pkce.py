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


def _follow_oauth_for_code(sess, url, max_hops: int = 15):
    """GET 手动跟随 OAuth 重定向链，提取 ?code=。全程带 cookie jar。

    oauth2/auth → consent(302) → oauth2/auth(consent_verifier) → callback?code=
    /api/accounts/consent 是 GET 跳转 hop（不是 POST API，POST 405 正常）。
    """
    from urllib.parse import urljoin as _uj, urlparse, parse_qs

    cur = url
    for _ in range(max_hops):
        if "code=" in cur:
            return parse_qs(urlparse(cur).query).get("code", [None])[0]
        try:
            r = sess.get(cur, headers=sess.auth_navigate_headers(referer="https://auth.openai.com/log-in/password"),
                         allow_redirects=False, timeout=30)
        except Exception as e:
            print(f"  [oauth] hop 异常: {str(e)[:50]}")
            return None
        loc = r.headers.get("location", "")
        if "code=" in loc:
            return parse_qs(urlparse(_uj(cur, loc)).query).get("code", [None])[0]
        if r.status_code in (301, 302, 303, 307, 308) and loc:
            cur = loc if loc.startswith("http") else _uj(cur, loc)
            continue
        if r.status_code == 200:
            fu = str(getattr(r, "url", cur))
            m = re.search(r"[?&]code=([^&]+)", fu)
            if m:
                return m.group(1)
            print(f"  [oauth] 停在 200: {fu[:60]}（可能需要 consent workspace/select 兜底）")
            return None
        print(f"  [oauth] hop 非重定向 status={r.status_code}")
        return None
    return None


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

    # 5. 完成回调链 → session；若停在 consent 200 HTML → workspace/select 提交 consent
    print(f"[5] follow_oauth_callback: {continue_url[:60]}")
    try:
        auth.follow_oauth_callback(sess, continue_url)
    except Exception as e:
        print(f"  callback 异常: {str(e)[:50]}")
    at = ""
    info = {}
    try:
        info = auth.fetch_session(sess)
        at = info.get("accessToken", "")
    except Exception:
        pass
    if not at:
        # consent 页未自动放行 → 手动 workspace/select
        print("[5b] consent workspace/select 提交")
        try:
            d = sess.get(f"{ISSUER}/api/accounts/client_auth_session_dump",
                         headers=sess.auth_api_headers(referer=f"{ISSUER}/email-verification"), timeout=20)
            ws = ((d.json().get("workspaces") or [{}])[0]).get("id")
            print(f"  workspace_id: {ws}")
            hc = sess.auth_api_headers(referer=f"{ISSUER}/")
            hc["content-type"] = "application/json"
            rw = sess.post(f"{ISSUER}/api/accounts/workspace/select",
                           headers=hc, data=json.dumps({"workspace_id": ws}), allow_redirects=False, timeout=30)
            print(f"  workspace/select -> {rw.status_code}")
            nxt = rw.json().get("continue_url", "") or rw.headers.get("location", "")
            orgs = rw.json().get("data", {}).get("orgs", [])
            if orgs and nxt:
                ob = {"org_id": orgs[0]["id"]}
                if orgs[0].get("projects"):
                    ob["project_id"] = orgs[0]["projects"][0]["id"]
                rorg = sess.post(f"{ISSUER}/api/accounts/organization/select",
                                 headers=hc, data=json.dumps(ob), allow_redirects=False, timeout=30)
                print(f"  organization/select -> {rorg.status_code}")
                nxt = rorg.json().get("continue_url", "") or rorg.headers.get("location", "") or nxt
            if nxt:
                auth.follow_oauth_callback(sess, nxt)
            info = auth.fetch_session(sess)
            at = info.get("accessToken", "")
        except Exception as e:
            print(f"  workspace 提交异常: {str(e)[:60]}")
    if at:
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

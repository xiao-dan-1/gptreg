#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""完整正常登录（非 signup）→ 新 token → 重查 add_password eligibility。

检验"会话类型假说"：OTP-only 账号的注册会话 is_signup=true 导致 add_password
ineligible；完整正常登录后的非 signup 会话是否翻转 eligibility？

流程（学自 enable_totp_api.py，但用邮箱 OTP 代替密码）：
  authorize → email-verification → 邮箱 OTP validate → continue_url → chatgpt 回调 → access_token
  解码新 token claims（is_signup/amr/idp）→ GET add_password/eligibility

用法: python capture/research/probe_normal_login_elig.py [email 子串] [--proxy URL]
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

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402

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


def _find_mail_account(main_email: str) -> dict:
    base = main_email.split("@")[0].split("+")[0] + "@" + main_email.split("@")[1]
    pool = Path("data/outlook_pool_ok.txt")
    for line in pool.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        a = parse_mail_line(line)
        if a and a["email"].split("@")[0].split("+")[0] + "@" + a["email"].split("@")[1] == base:
            return a
    raise RuntimeError(f"号池找不到主号 {base}")


def _inject_cookies(sess: BrowserSession, cookies: list[dict]) -> None:
    for c in cookies or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        domain = c.get("domain") or ""
        for d in (domain, domain.lstrip(".")):
            if not d:
                continue
            try:
                sess.session.cookies.set(c["name"], c["value"] or "", domain=d, path=c.get("path") or "/")
            except Exception:
                pass


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _decode_jwt(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sub = args[0] if args else "ElizabethJames"
    proxy_override = None
    if "--proxy" in sys.argv:
        proxy_override = sys.argv[sys.argv.index("--proxy") + 1]

    cfg = load_config()
    r = resolve_proxy(cfg, override=proxy_override)
    acc = _find_account(sub)
    email = acc["email"]
    main_email = email.split("+")[0] + "@" + email.split("@")[1]
    print(f"账号: {email}  主号: {main_email}")
    print(f"代理: {r.label()}")

    # 旧 token claims（注册会话）
    old_at = acc.get("access_token") or ""
    old_cl = _decode_jwt(old_at)
    oauth_claims = old_cl.get("https://api.openai.com/auth", {})
    print(f"\n旧 token（注册会话）: is_signup={old_cl.get('is_signup')} "
          f"amr={oauth_claims.get('amr')} idp={oauth_claims.get('idp')}")

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id
    _inject_cookies(sess, acc.get("session_cookies") or [])

    # 1. authorize
    code_verifier, code_challenge = _pkce()
    state = secrets.token_urlsafe(24)
    ap = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
          "scope": SCOPE, "code_challenge": code_challenge, "code_challenge_method": "S256", "state": state}
    authz_url = f"{ISSUER}/oauth/authorize?" + urlencode(ap)
    print("\n[1] GET /oauth/authorize")
    resp = sess.get(authz_url, headers=sess.auth_navigate_headers(referer="https://chatgpt.com/"),
                    allow_redirects=True, timeout=30)
    has_login = any(c.name == "login_session" for c in sess.session.cookies.jar)
    print(f"  -> {resp.status_code} login_session={has_login} final={str(getattr(resp, 'url', ''))[:60]}")

    # 2. authorize/continue 提交邮箱
    def _api_headers(referer):
        h = sess.auth_api_headers(referer=referer)
        h.pop("content-type", None)
        h["content-type"] = "application/json"
        return h

    print("[2] POST authorize/continue(邮箱)")
    tok_ac, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
    h = _api_headers(f"{ISSUER}/log-in")
    h["openai-sentinel-token"] = tok_ac
    resp2 = sess.post(f"{ISSUER}/api/accounts/authorize/continue",
                      headers=h, data=json.dumps({"username": {"kind": "email", "value": email}}),
                      allow_redirects=False, timeout=30)
    print(f"  -> {resp2.status_code}: {(resp2.text or '')[:150]}")
    if resp2.status_code != 200:
        print("  [x] authorize/continue 失败")
        return 1
    c2 = resp2.json()
    continue_url = c2.get("continue_url", "")
    page_type = (c2.get("page") or {}).get("type", "")
    print(f"  continue_url={continue_url[:60]} page_type={page_type}")

    # 3. 邮箱 OTP（OTP-only 无密码，跳过 password/verify）
    if page_type in ("email_otp_verification",) or "email-otp" in (continue_url or ""):
        print("[3] 邮箱 OTP")
        otp_after = time.time() - 3
        ma = _find_mail_account(main_email)
        client = build_mail_client(ma, proxy=None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"), cfg=cfg)
        otp = client.wait_for_otp(after_ts=otp_after, timeout=200, interval=3, settle_seconds=5)
        print(f"  OTP: {otp}")
        hdr = sess.auth_api_headers(referer=f"{ISSUER}/email-verification")
        hdr["openai-sentinel-token"] = tok_ac
        resp_otp = sess.post(f"{ISSUER}/api/accounts/email-otp/validate",
                             headers=hdr, data=json.dumps({"code": otp}), allow_redirects=False, timeout=30)
        print(f"  email-otp/validate -> {resp_otp.status_code}: {(resp_otp.text or '')[:200]}")
        if resp_otp.status_code == 200:
            c_otp = resp_otp.json()
            continue_url = c_otp.get("continue_url", "") or continue_url
            page_type = (c_otp.get("page") or {}).get("type", "") or page_type
        print(f"  after validate: continue_url={continue_url[:60]} page_type={page_type}")

    # 4. 从 continue_url 直接提取 OAuth code（validate 已返回 ac_ 开头的 code）
    print(f"[4] 提取 OAuth code")
    code = None
    m = re.search(r"[?&]code=([^&]+)", continue_url)
    if m:
        code = m.group(1)
        print(f"  从 continue_url 提取 code: {code[:25]}...")
    if not code:
        # 兜底：跟随回调链
        print("  跟随回调链找 code")
        url = continue_url
        nav = sess.auth_navigate_headers(referer=f"{ISSUER}/log-in")
        for hop in range(8):
            resp = sess.get(url, headers=nav, allow_redirects=False, timeout=30)
            loc = resp.headers.get("location", "")
            m2 = re.search(r"[?&]code=([^&]+)", loc or "")
            if m2:
                code = m2.group(1)
                print(f"  hop{hop} 拿到 code")
                break
            if not loc:
                fu = str(getattr(resp, "url", ""))
                m3 = re.search(r"[?&]code=([^&]+)", fu)
                if m3:
                    code = m3.group(1)
                    print(f"  hop{hop} 最终 URL 有 code")
                break
            url = loc if loc.startswith("http") else _uj(url, loc)
    if not code:
        print("  [x] 未拿到 code")
        return 2

    # 5. 建立会话：GET chatgpt callback（continue_url）→ 读 /api/auth/session
    print("[5] GET chatgpt callback + /api/auth/session")
    at = ""
    # 5a. 先试 /oauth/token（若 code 是 oauth code）
    resp_tok = sess.post(f"{ISSUER}/oauth/token",
                         headers=_api_headers(f"{ISSUER}/"),
                         data=json.dumps({"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": REDIRECT_URI, "client_id": CLIENT_ID,
                                          "code_verifier": code_verifier}), allow_redirects=False, timeout=30)
    if resp_tok.status_code == 200:
        tj = resp_tok.json()
        at = tj.get("access_token", "")
        print(f"  /oauth/token -> 200, at={at[:25]}...")
    else:
        print(f"  /oauth/token -> {resp_tok.status_code}")
    # 5b. 若失败：GET continue_url(callback) 建立 chatgpt 会话
    if not at:
        print("  走 callback 建立会话")
        cb = sess.get(continue_url, headers=sess.chatgpt_headers(), allow_redirects=True, timeout=30)
        print(f"  callback -> {cb.status_code} final={str(getattr(cb, 'url', ''))[:60]}")
        for attempt in range(5):
            try:
                sr = sess.get("https://chatgpt.com/api/auth/session", headers=sess.chatgpt_headers(), timeout=20)
                if sr.status_code == 200:
                    sj = sr.json()
                    if sj and sj.get("accessToken"):
                        at = sj["accessToken"]
                        print(f"  session -> accessToken: {at[:25]}...")
                        break
                    print(f"  session 无 accessToken: {str(sj)[:80]}")
                else:
                    print(f"  session HTTP {sr.status_code}")
            except Exception as e:
                print(f"  session 异常: {str(e)[:50]}")
            import time as _t
            _t.sleep(2)
    if not at:
        print("  [x] 未拿到 access_token")
        return 3
    print(f"  access_token: {at[:30]}...")

    # 6. 新 token claims（是否非 signup）
    new_cl = _decode_jwt(at)
    new_oauth = new_cl.get("https://api.openai.com/auth", {})
    print(f"\n新 token（正常登录）: is_signup={new_cl.get('is_signup')} "
          f"amr={new_oauth.get('amr')} idp={new_oauth.get('idp')}")
    print(f"  差异: is_signup {old_cl.get('is_signup')} -> {new_cl.get('is_signup')}")

    # 7. 用新 token 重查 add_password eligibility
    print("\n[7] 用新 token 重查 add_password eligibility")
    h6 = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h6["authorization"] = f"Bearer {at}"
    h6["oai-device-id"] = sess.device_id
    h6.pop("content-type", None)
    for p in ("/accounts/add_password/eligibility", "/accounts/change_password/eligibility",
              "/accounts/security_settings/info"):
        resp = sess.get("https://chatgpt.com/backend-api" + p, headers=h6, timeout=20)
        print(f"  GET {p} -> {resp.status_code}: {(resp.text or '')[:120]}")

    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

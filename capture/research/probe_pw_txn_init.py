#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探测 password/add 的"设密码事务"启动流程。

新鲜登录后，尝试：
  1. auth.openai.com 可能的启动端点
  2. chatgpt reauth 带不同 callbackUrl → 再 password/add

用法: python capture/research/probe_pw_txn_init.py [email 子串] [--proxy URL]
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

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402

PASSWORD = "ResearchSetPw2026!"
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
    for src in (Path("data/outlook_pool_ok.txt"), Path("data/research_pool_cloudmail.txt"),
                Path("data/research_pool_cm_totp.txt"), Path("mail_pool.txt")):
        if not src.exists():
            continue
        for line in src.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            a = parse_mail_line(line)
            if a and a["email"].split("@")[0].split("+")[0] + "@" + a["email"].split("@")[1] == base:
                return a
    raise RuntimeError(f"号池找不到主号 {base}")


def _inject_cookies(sess, cookies) -> None:
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


def _try_pwadd(sess, tag: str, new_pw: str) -> None:
    h = sess.auth_api_headers(referer=f"{ISSUER}/log-in/password")
    h["content-type"] = "application/json"
    try:
        resp = sess.post(f"{ISSUER}/api/accounts/password/add", headers=h,
                         data=json.dumps({"password": new_pw}), timeout=20)
        print(f"  [{tag}] password/add -> {resp.status_code}: {(resp.text or '')[:100]}")
        return resp.status_code
    except Exception as e:
        print(f"  [{tag}] password/add 异常: {str(e)[:50]}")
        return -1


def _full_login(sess, email, cfg, main_email, proxy_url):
    """完整登录：authorize → OTP → validate → callback，返回 continue_url(含code)。"""
    state = secrets.token_urlsafe(24)
    ap = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
          "scope": SCOPE, "state": state}
    authz_url = f"{ISSUER}/oauth/authorize?" + urlencode(ap)
    sess.get(authz_url, headers=sess.auth_navigate_headers(referer="https://chatgpt.com/"),
             allow_redirects=True, timeout=30)

    def _api_headers(referer):
        h = sess.auth_api_headers(referer=referer)
        h.pop("content-type", None)
        h["content-type"] = "application/json"
        return h

    tok_ac, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
    h = _api_headers(f"{ISSUER}/log-in")
    h["openai-sentinel-token"] = tok_ac
    resp2 = sess.post(f"{ISSUER}/api/accounts/authorize/continue",
                      headers=h, data=json.dumps({"username": {"kind": "email", "value": email}}),
                      allow_redirects=False, timeout=30)
    continue_url = resp2.json().get("continue_url", "") if resp2.status_code == 200 else ""

    otp_after = time.time() - 3
    ma = _find_mail_account(main_email)
    client = build_mail_client(ma, proxy=None,
                               impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"), cfg=cfg)
    otp = client.wait_for_otp(after_ts=otp_after, timeout=200, interval=3, settle_seconds=5)
    hdr = sess.auth_api_headers(referer=f"{ISSUER}/email-verification")
    hdr["openai-sentinel-token"] = tok_ac
    resp_otp = sess.post(f"{ISSUER}/api/accounts/email-otp/validate",
                         headers=hdr, data=json.dumps({"code": otp}), allow_redirects=False, timeout=30)
    if resp_otp.status_code == 200:
        continue_url = resp_otp.json().get("continue_url", "") or continue_url
    # 完成 callback 建立会话
    if continue_url:
        try:
            sess.get(continue_url, headers=sess.chatgpt_headers(), allow_redirects=True, timeout=30)
        except Exception:
            pass
    return continue_url


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
    print(f"账号: {email}")

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id
    _inject_cookies(sess, acc.get("session_cookies") or [])

    # 1. 完整登录
    print("\n[0] 完整登录")
    _full_login(sess, email, cfg, main_email, r.session_url)
    print("  登录完成，开始探测")

    # 2. 探测 auth.openai.com 启动端点
    print("\n=== A. auth.openai.com 启动端点探测 ===")
    h = sess.auth_api_headers(referer=f"{ISSUER}/log-in")
    h["content-type"] = "application/json"
    for p in ("/api/accounts/password/start", "/api/accounts/password/setup",
              "/api/accounts/security/password", "/api/accounts/reauthenticate",
              "/api/accounts/password/change/start", "/api/accounts/security/settings"):
        try:
            resp = sess.get(f"{ISSUER}{p}", headers=h, timeout=15)
            print(f"  GET {p} -> {resp.status_code}: {(resp.text or '')[:80]}")
        except Exception as e:
            print(f"  GET {p} -> 异常 {str(e)[:40]}")

    # 3. 尝试不同 callbackUrl 的 chatgpt reauth → password/add
    print("\n=== B. chatgpt reauth 带不同 callbackUrl → password/add ===")
    for cb in ("https://chatgpt.com/?action=enable_password",
               "https://chatgpt.com/?action=set_password",
               "https://chatgpt.com/#settings/Account",
               "https://chatgpt.com/settings/account"):
        try:
            auth.get_providers(sess)
            csrf = auth.get_csrf_token(sess)
            query = {"prompt": "login", "ext-oai-did": sess.device_id,
                     "reauth": "password", "max_age": "0", "login_hint": email,
                     "screen_hint": "login_or_signup"}
            url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(query)
            hh = sess.chatgpt_headers()
            hh["content-type"] = "application/x-www-form-urlencoded"
            hh["origin"] = "https://chatgpt.com"
            resp = sess.post(url, headers=hh, data=urlencode({"callbackUrl": cb, "csrfToken": csrf, "json": "true"}), timeout=30)
            auth_url = resp.json().get("url", "") if resp.status_code == 200 else ""
            final = auth.follow_authorize(sess, auth_url, attempts=1) if auth_url else ""
            print(f"  reauth cb={cb[:40]} -> 落点 {final[:50]}")
            # OTP if needed
            if "email-verification" in final:
                otp_after = time.time() - 3
                ma = _find_mail_account(main_email)
                client = build_mail_client(ma, proxy=None,
                                           impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"), cfg=cfg)
                otp = client.wait_for_otp(after_ts=otp_after, timeout=200, interval=3, settle_seconds=5)
                hdr = sess.auth_api_headers(referer=f"{ISSUER}/email-verification")
                tok2, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
                hdr["openai-sentinel-token"] = tok2
                sess.post(f"{ISSUER}/api/accounts/email-otp/validate",
                          headers=hdr, data=json.dumps({"code": otp}), allow_redirects=False, timeout=30)
            _try_pwadd(sess, f"reauth cb={cb[:20]}", PASSWORD)
        except Exception as e:
            print(f"  reauth {cb[:30]} 异常: {str(e)[:50]}")

    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

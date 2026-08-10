#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多点探测 password/add 正确触发时机。

在登录流程的不同点尝试 POST auth.openai.com/api/accounts/password/add，找会话状态机认的时刻。

流程：
  A. 完整 OAuth 登录（authorize → email OTP → validate → callback 建立会话）
  B. 在每个点尝试 password/add，记录响应

用法: python capture/research/probe_password_add_points.py [email 子串] [--new-password xxx] [--proxy URL]
"""
from __future__ import annotations

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

PASSWORD = "ResearchSetPw2026!"
ISSUER = "https://auth.openai.com"


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


def _try_password_add(sess, tag: str, new_pw: str) -> None:
    h = sess.auth_api_headers(referer=f"{ISSUER}/log-in/password")
    h["content-type"] = "application/json"
    try:
        resp = sess.post(f"{ISSUER}/api/accounts/password/add",
                         headers=h, data=json.dumps({"password": new_pw}), timeout=20)
        print(f"  [{tag}] password/add -> {resp.status_code}: {(resp.text or '')[:120]}")
    except Exception as e:
        print(f"  [{tag}] password/add 异常: {str(e)[:60]}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sub = args[0] if args else "ElizabethJames"
    new_pw = PASSWORD
    if "--new-password" in sys.argv:
        new_pw = sys.argv[sys.argv.index("--new-password") + 1]
    proxy_override = None
    if "--proxy" in sys.argv:
        proxy_override = sys.argv[sys.argv.index("--proxy") + 1]

    cfg = load_config()
    r = resolve_proxy(cfg, override=proxy_override)
    acc = _find_account(sub)
    email = acc["email"]
    main_email = email.split("+")[0] + "@" + email.split("@")[1]
    print(f"账号: {email}  新密码: {new_pw[:4]}***")

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id
    _inject_cookies(sess, acc.get("session_cookies") or [])

    # 0. 先试存储会话（可能 409）
    _try_password_add(sess, "存储会话(旧)", new_pw)

    # 1. authorize
    state = secrets.token_urlsafe(24)
    ap = {"response_type": "code", "client_id": "app_X8zY6vW2pQ9tR3dE7nK1jL5gH",
          "redirect_uri": "https://chatgpt.com/api/auth/callback/openai",
          "scope": "openid email profile offline_access model.request model.read organization.read organization.write",
          "state": state}
    authz_url = f"{ISSUER}/oauth/authorize?" + urlencode(ap)
    print("\n[1] GET /oauth/authorize")
    resp = sess.get(authz_url, headers=sess.auth_navigate_headers(referer="https://chatgpt.com/"),
                    allow_redirects=True, timeout=30)
    print(f"  -> {resp.status_code}")

    # 2. authorize/continue(邮箱)
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
    print(f"  -> {resp2.status_code}: {(resp2.text or '')[:100]}")
    continue_url = resp2.json().get("continue_url", "") if resp2.status_code == 200 else ""

    # 点 A：authorize/continue 后（可能还在验证前）
    _try_password_add(sess, "authorize/continue后", new_pw)

    # 3. 邮箱 OTP
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
    print(f"  email-otp/validate -> {resp_otp.status_code}")
    if resp_otp.status_code == 200:
        continue_url = resp_otp.json().get("continue_url", "") or continue_url

    # 点 B：OTP 验证后（会话应该已认证）
    _try_password_add(sess, "OTP验证后", new_pw)

    # 4. 跟随 continue_url 完成 callback
    if continue_url:
        print(f"[4] 跟随 callback: {continue_url[:60]}")
        try:
            cb = sess.get(continue_url, headers=sess.chatgpt_headers(), allow_redirects=True, timeout=30)
            print(f"  callback -> {cb.status_code}")
        except Exception as e:
            print(f"  callback 异常: {str(e)[:50]}")
        # 点 C：callback 后（完整会话）
        _try_password_add(sess, "callback后", new_pw)

    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

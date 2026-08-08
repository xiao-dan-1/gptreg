#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针：2FA(TOTP) 开启时纯协议登录链响应结构。

链: GET /oauth/authorize → POST authorize/continue(邮箱+sentinel) → POST password/verify(密码+sentinel)
→ dump password/verify 完整响应,识别 2FA/TOTP 验证要求与后续 endpoint。

用法: python capture/probe_totp_login.py [--email 账号关键字] [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"
SCOPE = "openid email profile offline_access model.request model.read organization.read organization.write"


def _find_account(email_contains: str) -> dict:
    for line in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="KathrynEverett6196")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    email = acc["email"]
    password = acc.get("password") or ""
    print(f"账号: {email}  密码: {password[:4]}***")

    r = resolve_proxy(cfg, override=args.proxy)
    s = BrowserSession(cfg, proxy=r.session_url)
    s.device_id = acc.get("device_id") or s.device_id
    print(f"代理: {r.label()}  device_id: {s.device_id}")

    def _api_headers(referer: str) -> dict:
        h = s.auth_api_headers(referer=referer)
        h.pop("content-type", None)
        h["content-type"] = "application/json"
        return h

    t0 = time.time()

    # 1. 用 signin_openai 建立 OAuth 会话(与注册流程同路径, 正确拿 login_session)
    auth.get_providers(s)
    time.sleep(0.3)
    csrf = auth.get_csrf_token(s)
    time.sleep(0.3)
    au = auth.signin_openai(s, csrf, email)
    time.sleep(0.3)
    final = auth.follow_authorize(s, au, attempts=1)
    has_login = any(c.name == "login_session" for c in s.session.cookies.jar)
    print(f"[1] signin_openai authorize 落点: {final[:80]}  login_session={has_login} ({(time.time()-t0):.0f}s)")

    # 2. authorize/continue(邮箱)
    tok_ac, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="authorize_continue", cfg=cfg)
    h = _api_headers(f"{ISSUER}/log-in")
    h["openai-sentinel-token"] = tok_ac
    resp2 = s.post(f"{ISSUER}/api/accounts/authorize/continue",
                   headers=h, data=json.dumps({"username": {"kind": "email", "value": email}}),
                   allow_redirects=False, timeout=30)
    print(f"[2] authorize/continue -> {resp2.status_code} ({(time.time()-t0):.0f}s)")
    if resp2.status_code != 200:
        print(f"    body: {(resp2.text or '')[:300]}")
        r.close()
        return 2
    c2 = resp2.json()
    print(f"    字段: {list(c2.keys())}")
    print(f"    page.type: {(c2.get('page') or {}).get('type')}")
    print(f"    continue_url: {str(c2.get('continue_url'))[:90]}")

    # 3. password/verify(密码) → 关键:dump 完整响应,看 2FA 要求
    tok_pw, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="password_verify", cfg=cfg)
    h = _api_headers(f"{ISSUER}/log-in/password")
    h["openai-sentinel-token"] = tok_pw
    resp3 = s.post(f"{ISSUER}/api/accounts/password/verify",
                   headers=h, data=json.dumps({"password": password}),
                   allow_redirects=False, timeout=30)
    print(f"[3] password/verify -> {resp3.status_code} ({(time.time()-t0):.0f}s)")
    print("[3] 完整响应:")
    try:
        j = resp3.json()
        print(json.dumps(j, ensure_ascii=False, indent=2)[:3500])
    except Exception:
        print((resp3.text or "")[:3500])
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

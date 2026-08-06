#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针: 2FA(TOTP) 激活后登录链响应 + 用 pyotp 码走通登录。

背景: KathrynEverett 2FA 已激活(mfa_enabled:true, secret=EAYQ3SIJPJG2WRQWLUDA5LN76B5FIKQT)。
密码账号 + TOTP 激活后, password/verify 应不再直接给 code, 而是要求 TOTP 6 位码。
本探针: dump 2FA 激活后 password/verify 完整响应 → 找 TOTP 验证 endpoint → pyotp 码提交 → 拿 code。

用法: python capture/probe_totp_login2.py --email 账号 --password 密码 --totp-secret SECRET [--proxy ...]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import time
from urllib.parse import urlencode

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402

ISSUER = "https://auth.openai.com"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="KathrynEverett6196+wy656x@outlook.com")
    ap.add_argument("--password", default="0NS9lq1nWLYE6b")
    ap.add_argument("--totp-secret", default="EAYQ3SIJPJG2WRQWLUDA5LN76B5FIKQT")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    r = resolve_proxy(cfg, override=args.proxy)
    s = BrowserSession(cfg, proxy=r.session_url)
    t0 = time.time()
    print(f"账号: {args.email}  代理: {r.label()}")

    try:
        # 1. signin_openai 建立会话
        auth.get_providers(s)
        time.sleep(0.3)
        csrf = auth.get_csrf_token(s)
        time.sleep(0.3)
        au = auth.signin_openai(s, csrf, args.email)
        time.sleep(0.3)
        final = auth.follow_authorize(s, au, attempts=1)
        print(f"[1] authorize 落点: {final[:70]}")
        time.sleep(0.3)

        def _api_headers(referer: str) -> dict:
            h = s.auth_api_headers(referer=referer)
            h.pop("content-type", None)
            h["content-type"] = "application/json"
            return h

        # 2. authorize/continue(邮箱)
        tok_ac, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="authorize_continue", cfg=cfg)
        h = _api_headers(f"{ISSUER}/log-in")
        h["openai-sentinel-token"] = tok_ac
        resp2 = s.post(f"{ISSUER}/api/accounts/authorize/continue",
                       headers=h, data=json.dumps({"username": {"kind": "email", "value": args.email}}),
                       allow_redirects=False, timeout=30)
        print(f"[2] authorize/continue -> {resp2.status_code}")
        if resp2.status_code != 200:
            print(f"    body: {(resp2.text or '')[:250]}")
            return 2

        # 3. password/verify(密码) → dump 完整响应(看 2FA 要求)
        tok_pw, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="password_verify", cfg=cfg)
        h = _api_headers(f"{ISSUER}/log-in/password")
        h["openai-sentinel-token"] = tok_pw
        resp3 = s.post(f"{ISSUER}/api/accounts/password/verify",
                       headers=h, data=json.dumps({"password": args.password}),
                       allow_redirects=False, timeout=30)
        print(f"[3] password/verify -> {resp3.status_code}")
        print(f"    完整响应: {(resp3.text or '')[:1800]}")
        if resp3.status_code != 200:
            print("    密码验证失败")
            return 3
        c3 = resp3.json()
        cont = c3.get("continue_url") or ""
        page_type = (c3.get("page") or {}).get("type") or ""
        factor_id = None
        try:
            factor_id = (c3.get("page") or {}).get("payload", {}).get("factor_id")
        except Exception:
            pass
        print(f"    continue_url: {cont[:90]}")
        print(f"    page.type: {page_type}")
        print(f"    factor_id: {factor_id}")

        # 4. 若要求 TOTP → 用 pyotp 码验证(mfa/verify 需 type 参数)
        import pyotp
        code6 = pyotp.TOTP(args.totp_secret).now()
        print(f"\n[4] TOTP 6位码: {code6}")

        totp_needed = ("mfa" in page_type.lower()) or ("totp" in page_type.lower()) \
            or ("mfa" in cont.lower()) or ("totp" in cont.lower()) or ("authenticator" in cont.lower())
        if totp_needed or not cont:
            print("    检测到 TOTP 验证要求, 试候选 endpoint")
            candidates = [
                ("/api/accounts/mfa/verify", {"type": "totp", "id": factor_id, "code": code6}),
                ("/api/accounts/mfa/verify", {"type": "totp", "code": code6}),
                ("/api/accounts/mfa/verify", {"type": "email", "id": "email-otp", "code": code6}),
            ]
            for path, payload in candidates:
                try:
                    rc = s.post(f"{ISSUER}{path}", headers=_api_headers(f"{ISSUER}/log-in/password"),
                                data=json.dumps(payload), allow_redirects=False, timeout=30)
                    print(f"    POST {path} -> {rc.status_code}: {(rc.text or '')[:250]}")
                except Exception as exc:
                    print(f"    POST {path} 异常: {str(exc)[:80]}")
                time.sleep(0.4)
                if rc.status_code == 200:
                    try:
                        nj = rc.json()
                        cont = nj.get("continue_url") or cont
                        print(f"    -> 更新 continue_url: {cont[:80]}")
                    except Exception:
                        pass
        else:
            print("    password/verify 直接给 code(2FA 未要求?)")

        # 5. 若拿到 code → callback → at → check
        if cont and "callback" in cont:
            print(f"\n[5] follow callback: {cont[:70]}")
            auth.follow_oauth_callback(s, cont)
            info = auth.fetch_session(s)
            at = info.get("accessToken")
            if at:
                print(f"    at 前30: {at[:30]}")
                health = auth.check_account_health(s, at)
                print(f"    健康: {health.get('status')}")
            else:
                print("    无 accessToken")
        else:
            print("\n[5] 无 callback URL(登录未完成)")

        print(f"\n[总耗时] {(time.time()-t0):.0f}s")
    finally:
        r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

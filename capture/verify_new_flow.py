#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实验: OpenAI 注册流程变化后的新顺序——先 email-verification 验证邮箱, 再 register(设密码)。

背景: 批量全失败 register 400 invalid_auth_step, authorize 落点 email-verification
(旧流程落 create-account/password 时 register 直接成功)。怀疑新流程要求先验证邮箱。

新顺序:
  signin → authorize(落 email-verification) → send_otp → 收码 → validate
  → register(设密码) → create_account → callback → at

用法: python capture/verify_new_flow.py --email 主号 [--proxy 动态或固定]
"""
from __future__ import annotations

import json
import random
import string
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config, resolve_path  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client, mail_identity_key, UsedCodeCache  # noqa: E402
from gptreg.pipeline_otp import _root  # noqa: E402

FLOW_PWD = "username_password_create"
FLOW_OAUTH = "oauth_create_account"
REGISTER_URL = "https://auth.openai.com/api/accounts/user/register"
PASSWORD_REFERER = "https://auth.openai.com/create-account/password"
ABOUT_YOU_REFERER = "https://auth.openai.com/about-you"
SEND_OTP_URL = "https://auth.openai.com/api/accounts/email-otp/send"


def _base(m: str) -> str:
    return (m or "").split("@")[0].split("+")[0]


def _find_pool_account(cfg, base_email: str) -> dict:
    for line in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
        a = parse_mail_line(line.strip())
        if not a:
            continue
        if _base(a["email"]) == _base(base_email):
            return a
    raise RuntimeError(f"号池找不到 {base_email}")


def main() -> int:
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="PaulTorres9077@outlook.com")
    ap.add_argument("--proxy", default="")
    args = ap.parse_args()

    cfg = load_config()
    account = _find_pool_account(cfg, args.email)
    base_email = account["email"]
    name, dom = base_email.split("@")
    tag = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    email = f"{name}+{tag}@{dom}"
    password = "".join(random.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(14))
    display_name, bday = "James Miller", "1998-05-12"
    print(f"注册邮箱: {email}  收码主号: {base_email}")

    # 动态代理(默认)
    proxy_url = args.proxy
    if not proxy_url:
        import re
        tpl = str((cfg.get("proxy") or {}).get("dynamic", {}).get("template") or "")
        if tpl:
            sid = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))
            proxy_url = re.sub(r"-sid-[a-zA-Z0-9]+-t-", f"-sid-{sid}-t-", tpl)
        else:
            proxy_url = "http://127.0.0.1:10808"
    print(f"代理: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")

    resolved = resolve_proxy(cfg, override=proxy_url)
    s = BrowserSession(cfg, proxy=resolved.session_url)
    st = {"start": time.time()}

    def _el(tag: str):
        print(f"    [{tag}] {(time.time()-st['start']):.1f}s")

    try:
        # 1. signin → authorize(落 email-verification)
        auth.get_providers(s)
        time.sleep(0.3)
        csrf = auth.get_csrf_token(s)
        time.sleep(0.3)
        au = auth.signin_openai(s, csrf, email)
        time.sleep(0.3)
        final = auth.follow_authorize(s, au, attempts=1)
        print(f"[1] authorize 落点: {final[:75]}")
        _el("authorize")

        # 2. send_otp(先发码, 无 register)
        print("\n[2] send_otp(先发码)")
        r = s.get(SEND_OTP_URL, headers=s.auth_navigate_headers(referer=PASSWORD_REFERER),
                  allow_redirects=True)
        print(f"    HTTP {r.status_code} 落点: {str(getattr(r, 'url', ''))[:60]}")
        _el("send_otp")

        # 3. 收码 + validate(验证邮箱)
        print("\n[3] 收码 + validate")
        client = build_mail_client(account, proxy=resolved.session_url or None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
        identity = mail_identity_key(account)
        cache_path = resolve_path(cfg.get("mail", {}).get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
        used_cache = UsedCodeCache(cache_path)
        exclude = used_cache.seen_codes(identity)
        otp_timeout = int(cfg.get("mail", {}).get("otp_wait", 150) or 150)
        otp = client.wait_for_otp(after_ts=st["start"], timeout=otp_timeout,
                                  interval=3, settle_seconds=5, exclude_codes=exclude)
        used_cache.remember(identity, otp, email=email, status="submitted")
        print(f"    OTP: {otp} ({time.time()-st['start']:.1f}s)")
        vr = auth.validate_email_otp(s, otp, None)
        print(f"    validate: {str(vr)[:250]}")
        _el("validate")

        # 4. register(设密码)——看新顺序是否成功
        print("\n[4] register(设密码, 邮箱已验证后)")
        token, so_header = get_sentinel_token_via_quickjs(s, s.device_id, flow=FLOW_PWD, cfg=cfg)
        h = s.auth_api_headers(referer=PASSWORD_REFERER)
        h["openai-sentinel-token"] = token
        resp = s.post(REGISTER_URL, headers=h, data=json.dumps({"username": email, "password": password}))
        print(f"    HTTP {resp.status_code}: {(resp.text or '')[:250]}")
        _el("register")
        if resp.status_code != 200:
            print("\n[x] register 仍失败(新顺序不成立)")
            return 2
        reg = resp.json()
        print("    [OK] register 成功! 邮箱先验证后设密码成立")

        # 5. create_account + callback + at(完整链)
        print("\n[5] create_account + callback + at")
        send_url = reg.get("continue_url") or SEND_OTP_URL
        tok2, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow=FLOW_OAUTH, cfg=cfg)
        h2 = s.auth_api_headers(referer=ABOUT_YOU_REFERER)
        h2["openai-sentinel-token"] = tok2
        resp2 = s.post("https://auth.openai.com/api/accounts/create_account",
                       headers=h2, data=json.dumps({"name": display_name, "birthdate": bday}))
        print(f"    create_account: HTTP {resp2.status_code} {(resp2.text or '')[:200]}")
        if resp2.status_code == 200:
            cr = resp2.json()
            cu = cr.get("continue_url") or cr.get("url")
            if cu:
                auth.follow_oauth_callback(s, cu)
                info = auth.fetch_session(s)
                at = info.get("accessToken")
                print(f"    at 前30: {str(at)[:30]}")
                if at:
                    health = auth.check_account_health(s, at)
                    print(f"    健康: {health.get('status')}")
        print(f"\n[总耗时] {(time.time()-st['start']):.1f}s")
    finally:
        resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

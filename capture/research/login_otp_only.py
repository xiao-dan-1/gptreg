#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""无密码（OTP-only）账号纯协议登录：signin/openai → 邮箱 OTP → 新 token。

流程: signin/openai(login) → authorize → email-verification → 邮箱 OTP validate
→ continue_url → chatgpt 回调 → /api/auth/session → access_token → health

用法: python capture/research/login_otp_only.py [email 子串] [--proxy URL]
"""
from __future__ import annotations

import json
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
from gptreg.health import check_account_health  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402


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
    if base.endswith(".xdauv.xyz"):
        return {"email": base, "mail_type": "cloudmail", "raw_line": base}
    for src in (Path("data/outlook_pool_ok.txt"), Path("mail_pool.txt")):
        if not src.exists():
            continue
        for line in src.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            a = parse_mail_line(line)
            if a and a["email"].split("@")[0].split("+")[0] + "@" + a["email"].split("@")[1] == base:
                return a
    raise RuntimeError(f"号池找不到主号 {base}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sub = args[0] if args else "87fd6e"
    proxy_override = None
    if "--proxy" in sys.argv:
        proxy_override = sys.argv[sys.argv.index("--proxy") + 1]

    cfg = load_config()
    r = resolve_proxy(cfg, override=proxy_override)
    acc = _find_account(sub)
    email = acc["email"]
    main_email = email.split("@")[0].split("+")[0] + "@" + email.split("@")[1]
    print(f"账号: {email}  主号: {main_email}")
    print(f"代理: {r.label()}")

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id

    # 1. signin/openai（登录）
    auth.get_providers(sess)
    csrf = auth.get_csrf_token(sess)
    query = {"prompt": "login", "ext-oai-did": sess.device_id,
             "login_hint": email, "screen_hint": "login_or_signup"}
    h = sess.chatgpt_headers()
    h["content-type"] = "application/x-www-form-urlencoded"
    h["origin"] = "https://chatgpt.com"
    resp = sess.post("https://chatgpt.com/api/auth/signin/openai?" + urlencode(query),
                     headers=h, data=urlencode({"callbackUrl": "https://chatgpt.com/",
                                                "csrfToken": csrf, "json": "true"}), timeout=30)
    print(f"[1] signin/openai -> {resp.status_code}")
    auth_url = resp.json().get("url", "") if resp.status_code == 200 else ""
    final = auth.follow_authorize(sess, auth_url, attempts=2)
    print(f"[2] 落点: {final[:70]}")

    # 2. 邮箱 OTP
    if "email-verification" in final:
        print("[3] 邮箱 OTP 登录")
        otp_after = time.time() - 3
        ma = _find_mail_account(main_email)
        client = build_mail_client(ma, proxy=None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"), cfg=cfg)
        otp = client.wait_for_otp(after_ts=otp_after, timeout=200, interval=3, settle_seconds=5)
        print(f"  OTP: {otp}")
        sentinel_otp, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
        hdr = sess.auth_api_headers(referer="https://auth.openai.com/email-verification")
        hdr["openai-sentinel-token"] = sentinel_otp
        vr = sess.post("https://auth.openai.com/api/accounts/email-otp/validate",
                       headers=hdr, data=json.dumps({"code": otp}), allow_redirects=False, timeout=30)
        print(f"  validate -> {vr.status_code}")
        if vr.status_code == 200:
            continue_url = vr.json().get("continue_url", "")
            page_type = (vr.json().get("page") or {}).get("type", "")
            print(f"  page_type={page_type} continue_url={continue_url[:60]}")
            # 若 TOTP 挑战
            if "mfa" in page_type.lower() or "mfa" in (continue_url or ""):
                import pyotp
                code6 = pyotp.TOTP(acc.get("totp_secret") or "").now()
                fid = (vr.json().get("page") or {}).get("payload", {}).get("factor_id")
                h4 = sess.auth_api_headers(referer="https://auth.openai.com/")
                h4["content-type"] = "application/json"
                r4 = sess.post("https://auth.openai.com/api/accounts/mfa/verify",
                               headers=h4, data=json.dumps({"type": "totp", "id": fid, "code": code6}),
                               allow_redirects=False, timeout=30)
                print(f"  TOTP verify -> {r4.status_code}")
                if r4.status_code == 200:
                    continue_url = r4.json().get("continue_url", "") or continue_url
            # 跟随 continue_url → chatgpt 回调
            if continue_url:
                try:
                    cb = sess.get(continue_url, headers=sess.chatgpt_headers(), allow_redirects=True, timeout=30)
                    print(f"[4] callback -> {cb.status_code}")
                except Exception as e:
                    print(f"  callback 异常: {str(e)[:50]}")

    # 3. 拿新 token
    at = ""
    for attempt in range(5):
        try:
            sr = sess.get("https://chatgpt.com/api/auth/session", headers=sess.chatgpt_headers(), timeout=20)
            if sr.status_code == 200:
                sj = sr.json()
                if sj and sj.get("accessToken"):
                    at = sj["accessToken"]
                    print(f"[5] 新 access_token: {at[:30]}...")
                    break
                print(f"  session 无 token: {str(sj)[:80]}")
        except Exception as e:
            print(f"  session 异常: {str(e)[:50]}")
        time.sleep(2)

    # 4. 健康检查
    if at:
        hc = check_account_health(sess, at)
        print(f"[6] health: status={hc.get('status')} http={hc.get('http')}")
        print(f"\n{'✅ 无密码账号 OTP 登录成功，新 token 获取' if hc.get('status') == 'ok' else '❌ 健康检查失败'}")
        return 0 if hc.get("status") == "ok" else 3
    print("  [x] 未拿到 access_token")
    r.close()
    return 4


if __name__ == "__main__":
    raise SystemExit(main())

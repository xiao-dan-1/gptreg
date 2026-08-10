#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OTP-only 账号 reauth → TOTP enroll 全流程（无需密码）。

背景: mfa/enroll 需 recent_auth_required（重认证）。OTP-only 账号无密码，
重认证走 chatgpt signin/openai 带 reauth=password&max_age=0 → 邮箱 OTP → 新鲜 token。

用法: python capture/research/reauth_enroll_otp.py [email 子串] [--proxy URL]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
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

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id
    _inject_cookies(sess, acc.get("session_cookies") or [])

    # 1. reauth signin（带 reauth=password&max_age=0）
    auth.get_providers(sess)
    csrf = auth.get_csrf_token(sess)
    query = {
        "prompt": "login",
        "ext-oai-did": sess.device_id,
        "reauth": "password",
        "max_age": "0",
        "login_hint": email,
        "screen_hint": "login_or_signup",
    }
    url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(query)
    h = sess.chatgpt_headers()
    h["content-type"] = "application/x-www-form-urlencoded"
    h["origin"] = "https://chatgpt.com"
    body = urlencode({"callbackUrl": "https://chatgpt.com/?action=enable&factor=totp",
                      "csrfToken": csrf, "json": "true"})
    print("\n[1] reauth signin/openai (reauth=password&max_age=0)")
    resp = sess.post(url, headers=h, data=body, timeout=30)
    print(f"  -> {resp.status_code}: {(resp.text or '')[:150]}")
    if resp.status_code != 200:
        print("  [x] reauth signin 失败")
        return 1
    try:
        auth_url = resp.json().get("url", "")
    except Exception:
        auth_url = ""
    if not auth_url:
        print("  [x] 无 authorize url")
        return 2
    print(f"  authorize_url: {auth_url[:90]}")

    # 2. follow authorize → 落点
    print("[2] follow authorize")
    final = auth.follow_authorize(sess, auth_url, attempts=2)
    print(f"  落点: {final[:100]}")

    # 3. 若 email-verification → 收 OTP → validate
    if "email-verification" in final or "email-otp" in final:
        print("[3] 邮箱 OTP 重认证")
        otp_after = time.time() - 3  # 宽松(避免 fast OTP 被当旧件)
        mail_account = _find_mail_account(main_email)
        client = build_mail_client(mail_account, proxy=None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"), cfg=cfg)
        otp = client.wait_for_otp(after_ts=otp_after, timeout=200, interval=3, settle_seconds=5)
        print(f"  OTP: {otp}")
        sentinel_otp, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
        vr = auth.validate_email_otp(sess, otp, sentinel_otp)
        print(f"  validate -> {getattr(vr, 'status_code', '?')}: {(getattr(vr, 'text', '') or '')[:150]}")
        auth.maybe_follow_external(sess, vr)
        final = str(getattr(vr, 'url', '') or final)
        print(f"  after validate 落点: {final[:90]}")
    else:
        print(f"  落点不是 email-verification: {final[:80]}")
        # 可能已到 chatgpt 回调
        pass

    # 4. 拿 chatgpt 会话 / 新 access_token
    print("[4] GET /api/auth/session 换新 token")
    st = None
    for attempt in range(5):
        try:
            sr = sess.get("https://chatgpt.com/api/auth/session", headers=sess.chatgpt_headers(), timeout=20)
            if sr.status_code == 200:
                sj = sr.json()
                if sj and sj.get("accessToken"):
                    st = sj["accessToken"]
                    print(f"  新 access_token: {st[:25]}...")
                    break
                print(f"  session 响应无 accessToken: {str(sj)[:100]}")
            else:
                print(f"  session HTTP {sr.status_code}")
        except Exception as e:
            print(f"  session 异常: {e}")
        time.sleep(2)
    if not st:
        print("  [x] 未拿到新 access_token")
        return 3

    # 5. mfa/enroll
    print("[5] POST mfa/enroll")
    h6 = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h6["authorization"] = f"Bearer {st}"
    h6["oai-device-id"] = sess.device_id
    h6["content-type"] = "application/json"
    resp = sess.post("https://chatgpt.com/backend-api/accounts/mfa/enroll",
                     headers=h6, data=json.dumps({"factor_type": "totp"}), timeout=30)
    print(f"  -> {resp.status_code}: {(resp.text or '')[:300]}")
    if resp.status_code != 200:
        print("  [x] enroll 失败")
        return 4
    ej = resp.json()
    secret = str(ej.get("secret") or "")
    session_id = ej.get("session_id")
    factor_id = (ej.get("factor") or {}).get("id")
    if not secret:
        m = re.search(r"[?&]secret=([A-Z2-7]+)", resp.text)
        if m:
            secret = m.group(1)
    if not secret:
        print(f"  [x] 无 secret: {resp.text[:300]}")
        return 5
    print(f"  TOTP secret: {secret}")

    # 6. activate_enrollment
    import pyotp
    code6 = pyotp.TOTP(secret).now()
    print(f"[6] activate_enrollment code={code6}")
    resp2 = sess.post("https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment",
                      headers=h6, data=json.dumps({"code": code6, "session_id": session_id,
                                                    "factor_id": factor_id, "factor_type": "totp"}), timeout=30)
    print(f"  -> {resp2.status_code}: {(resp2.text or '')[:200]}")
    ok = resp2.status_code == 200 and '"success":true' in (resp2.text or "")
    print(f"  激活: {'成功' if ok else '失败'}")

    # 7. 保存 + 重测 add_password eligibility
    if ok:
        from gptreg.account_store import save_account
        acc2 = dict(acc)
        acc2["totp_secret"] = secret
        acc2["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_account(cfg, record=acc2)
        print(f"  ✅ TOTP 已保存: {email}  secret={secret}")
        # 重测 add_password
        h7 = dict(h6)
        resp3 = sess.get("https://chatgpt.com/backend-api/accounts/add_password/eligibility", headers=h7, timeout=20)
        print(f"  add_password/eligibility(开 TOTP 后) -> {resp3.status_code}: {(resp3.text or '')[:120]}")

    r.close()
    return 0 if ok else 6


if __name__ == "__main__":
    raise SystemExit(main())

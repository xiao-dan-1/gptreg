#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OTP-only 账号 reauth → 设密码（auth.openai.com/api/accounts/password/add）。

流程: reauth signin(reauth=password&max_age=0) → 邮箱 OTP → 新鲜 auth 会话
      → POST /api/accounts/password/add {password} → 补密码成功

用法: python capture/research/reauth_set_password.py [email 子串] [--new-password xxx] [--proxy URL]
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
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402

PASSWORD = "ResearchSetPw2026!"  # 默认测试密码


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
    # cloudmail 域：动态账号，无池凭据，直接构造（CloudMailClient 用 admin 配置拉码）
    if base.endswith((".xdauv.xyz",)):
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
    main_email = email.split("@")[0].split("+")[0] + "@" + email.split("@")[1]
    print(f"账号: {email}  主号: {main_email}  新密码: {new_pw[:4]}***")
    print(f"代理: {r.label()}")

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id
    _inject_cookies(sess, acc.get("session_cookies") or [])

    # 1. reauth signin（关键：post_login_add_password=true 建立设密码事务）
    auth.get_providers(sess)
    csrf = auth.get_csrf_token(sess)
    query = {"prompt": "login", "ext-oai-did": sess.device_id,
             "reauth": "password", "max_age": "0", "login_hint": email,
             "screen_hint": "login_or_signup",
             "post_login_add_password": "true"}
    url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(query)
    h = sess.chatgpt_headers()
    h["content-type"] = "application/x-www-form-urlencoded"
    h["origin"] = "https://chatgpt.com"
    body = urlencode({"callbackUrl": "https://chatgpt.com/", "csrfToken": csrf, "json": "true"})
    print("\n[1] reauth signin")
    resp = sess.post(url, headers=h, data=body, timeout=30)
    auth_url = resp.json().get("url", "") if resp.status_code == 200 else ""
    print(f"  -> {resp.status_code} auth_url={auth_url[:70]}")

    # 2. follow authorize
    print("[2] follow authorize")
    final = auth.follow_authorize(sess, auth_url, attempts=2)
    print(f"  落点: {final[:80]}")

    # 3. 邮箱 OTP
    if "email-verification" in final or "email-otp" in final:
        print("[3] 邮箱 OTP")
        otp_after = time.time() - 3
        mail_account = _find_mail_account(main_email)
        client = build_mail_client(mail_account, proxy=None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"), cfg=cfg)
        otp = client.wait_for_otp(after_ts=otp_after, timeout=200, interval=3, settle_seconds=5)
        print(f"  OTP: {otp}")
        sentinel_otp, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
        vr = auth.validate_email_otp(sess, otp, sentinel_otp)
        auth.maybe_follow_external(sess, vr)
        print(f"  validate OK, 落点: {str(getattr(vr, 'url', '') or final)[:80]}")

    # 4. POST /api/accounts/password/add（关键：新鲜 auth 会话下设密码）
    print(f"[4] POST /api/accounts/password/add {{password}}")
    h4 = sess.auth_api_headers(referer="https://auth.openai.com/")
    h4["content-type"] = "application/json"
    resp4 = sess.post("https://auth.openai.com/api/accounts/password/add",
                      headers=h4, data=json.dumps({"password": new_pw}), timeout=30)
    print(f"  -> {resp4.status_code}: {(resp4.text or '')[:200]}")
    if resp4.status_code == 200:
        print("  ✅ 补密码成功!")
        # 更新账号记录
        from gptreg.account_store import save_account
        acc2 = dict(acc)
        acc2["password"] = new_pw
        acc2["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_account(cfg, record=acc2)
        print(f"  已保存 password 到 accounts.jsonl")
        r.close()
        return 0
    else:
        print(f"  [x] 补密码失败")
        # 调试：看具体错误
        try:
            ej = resp4.json()
            print(f"  detail: {json.dumps(ej, ensure_ascii=False)[:200]}")
        except Exception:
            pass
        r.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

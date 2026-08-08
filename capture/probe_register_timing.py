#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实测一次注册(2FA), 分步记录耗时 + 响应 + 反馈质量, 便于定位缺反馈/卡点。

用链式代理(已确认正常)。流程: signin → authorize → register → send_otp → OTP → validate
→ create_account → callback → at → enroll → activate。

反馈质量标记:
  [OK]   有明确状态+耗时
  [弱]   有状态但缺耗时/细节
  [缺失] 无输出/异常被吞

用法: python capture/probe_register_timing.py [--email 主号]
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
from gptreg.register_otp import _root  # noqa: E402

FLOW_PWD = "username_password_create"
FLOW_OAUTH = "oauth_create_account"
REGISTER_URL = "https://auth.openai.com/api/accounts/user/register"
PASSWORD_REFERER = "https://auth.openai.com/create-account/password"
ABOUT_YOU_REFERER = "https://auth.openai.com/about-you"


def _base(m: str) -> str:
    return (m or "").split("@")[0].split("+")[0]


def _find_pool_account(base_email: str) -> dict:
    for line in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
        a = parse_mail_line(line.strip())
        if not a:
            continue
        if _base(a["email"]) == _base(base_email):
            return a
    raise RuntimeError(f"号池找不到 {base_email}")


class _T:
    """分步耗时记录。"""

    def __init__(self):
        self.steps: list[tuple[str, float]] = []

    def mark(self, name: str):
        self.steps.append((name, time.time()))

    def dump(self, t0: float):
        print("\n=== 分步耗时 ===")
        prev = t0
        for name, t in self.steps:
            print(f"  {name:28s} {(t-prev)*1000:7.0f} ms   (累计 {(t-t0):.1f}s)")
            prev = t
        print(f"  {'总耗时':28s} {(time.time()-t0)*1000:7.0f} ms")


def main() -> int:
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="ElizabethJames6948@outlook.com")
    args = ap.parse_args()

    cfg = load_config()
    account = _find_pool_account(args.email)
    base_email = account["email"]
    name, dom = base_email.split("@")
    tag = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    email = f"{name}+{tag}@{dom}"
    password = "".join(random.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(14))
    print(f"注册邮箱: {email}  收码主号: {base_email}")

    t0 = time.time()
    T = _T()
    resolved = resolve_proxy(cfg)  # 链式动态代理(已确认正常)
    s = BrowserSession(cfg, proxy=resolved.session_url)
    print(f"代理: {resolved.label()}")

    try:
        # 1. get_providers + csrf
        auth.get_providers(s)
        T.mark("get_providers")
        csrf = auth.get_csrf_token(s)
        T.mark("get_csrf")
        print(f"[OK] csrf 拿到")

        # 2. signin_openai
        au = auth.signin_openai(s, csrf, email)
        T.mark("signin_openai")
        print(f"[OK] signin 返回 authorize_url")

        # 3. follow_authorize → 落点
        final = auth.follow_authorize(s, au, attempts=1)
        T.mark("follow_authorize")
        print(f"[OK] authorize 落点: {final[:75]}")

        # 4. sentinel quickjs(register flow)
        t_s = time.time()
        token, so_header = get_sentinel_token_via_quickjs(s, s.device_id, flow=FLOW_PWD, cfg=cfg)
        T.mark("sentinel_quickjs(register)")
        print(f"[OK] sentinel token t_len={len(token)} so_len={len(so_header or '')} ({(time.time()-t_s):.1f}s)")

        # 5. register
        t_r = time.time()
        h = s.auth_api_headers(referer=PASSWORD_REFERER)
        h["openai-sentinel-token"] = token
        resp = s.post(REGISTER_URL, headers=h, data=json.dumps({"username": email, "password": password}))
        T.mark("register")
        print(f"[{'OK' if resp.status_code == 200 else '弱'}] register -> {resp.status_code} ({(time.time()-t_r):.1f}s)")
        print(f"    响应: {(resp.text or '')[:300]}")
        if resp.status_code != 200:
            T.dump(t0)
            print("\n[x] register 失败, 停止")
            return 2
        reg = resp.json()

        # 6. send_otp
        t_so = time.time()
        send_url = reg.get("continue_url") or "https://auth.openai.com/api/accounts/email-otp/send"
        r = s.get(send_url, headers=s.auth_navigate_headers(referer=PASSWORD_REFERER), allow_redirects=True)
        T.mark("send_otp")
        print(f"[OK] send_otp -> {r.status_code} 落点={str(getattr(r, 'url', ''))[:60]} ({(time.time()-t_so):.1f}s)")

        # 7. 收码
        t_o = time.time()
        client = build_mail_client(account, proxy=resolved.session_url or None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
        identity = mail_identity_key(account)
        cache_path = resolve_path(cfg.get("mail", {}).get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
        used_cache = UsedCodeCache(cache_path)
        exclude = used_cache.seen_codes(identity)
        otp_timeout = int(cfg.get("mail", {}).get("otp_wait", 150) or 150)
        otp = client.wait_for_otp(after_ts=t0, timeout=otp_timeout, interval=3, settle_seconds=5,
                                  exclude_codes=exclude)
        T.mark("收码")
        used_cache.remember(identity, otp, email=email, status="submitted")
        print(f"[OK] OTP 收到 {otp} ({(time.time()-t_o):.1f}s)")

        # 8. validate
        t_v = time.time()
        vr = auth.validate_email_otp(s, otp, None)
        T.mark("validate_otp")
        print(f"[OK] validate -> {str(vr)[:200]} ({(time.time()-t_v):.1f}s)")

        # 9. create_account
        t_c = time.time()
        tok2, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow=FLOW_OAUTH, cfg=cfg)
        T.mark("sentinel(create)")
        so_b = None
        try:
            from gptreg.browser_sentinel import harvest_browser_sentinel
            br = harvest_browser_sentinel(cfg, flow=FLOW_OAUTH, device_id=s.device_id,
                                          proxy=resolved.session_url, headless=True, timeout_s=90)
            if br.get("ok") and br.get("so_header"):
                so_b = br["so_header"]
                print(f"[OK] browser so len={len(so_b)}")
            else:
                print(f"[弱] browser so 采集失败: {br.get('error')}")
        except Exception as exc:
            print(f"[弱] browser so 异常: {type(exc).__name__}: {str(exc)[:80]}")
        T.mark("browser_so")
        h2 = s.auth_api_headers(referer=ABOUT_YOU_REFERER)
        h2["openai-sentinel-token"] = tok2
        if so_b:
            h2["openai-sentinel-so-token"] = so_b
        resp2 = s.post("https://auth.openai.com/api/accounts/create_account",
                       headers=h2, data=json.dumps({"name": "James Miller", "birthdate": "1998-05-12"}))
        T.mark("create_account")
        print(f"[OK] create_account -> {resp2.status_code} ({(time.time()-t_c):.1f}s)")
        print(f"    响应: {(resp2.text or '')[:200]}")
        if resp2.status_code != 200:
            T.dump(t0)
            return 3
        cr = resp2.json()
        cu = cr.get("continue_url") or cr.get("url")
        if not cu:
            print("[弱] create_account 200 但无 continue_url")
            T.dump(t0)
            return 4

        # 10. callback + at
        t_at = time.time()
        auth.follow_oauth_callback(s, cu)
        info = auth.fetch_session(s)
        at = info.get("accessToken")
        T.mark("callback+session")
        if not at:
            print("[弱] callback 后无 accessToken")
            T.dump(t0)
            return 5
        print(f"[OK] access_token 前30: {at[:30]} ({(time.time()-t_at):.1f}s)")

        # 11. enroll
        h6 = s.chatgpt_headers(referer="https://chatgpt.com/")
        h6["authorization"] = f"Bearer {at}"
        h6["oai-device-id"] = s.device_id
        h6.pop("content-type", None)
        h6["content-type"] = "application/json"
        t_e = time.time()
        resp_e = s.post("https://chatgpt.com/backend-api/accounts/mfa/enroll",
                        headers=h6, data=json.dumps({"factor_type": "totp"}), timeout=30)
        T.mark("enroll")
        print(f"[OK] enroll -> {resp_e.status_code} ({(time.time()-t_e):.1f}s): {resp_e.text[:200]}")
        if resp_e.status_code != 200:
            T.dump(t0)
            return 6
        ej = resp_e.json()
        sec = ej.get("secret")
        sid_ = ej.get("session_id")
        fid = (ej.get("factor") or {}).get("id")

        # 12. activate
        import pyotp
        code6 = pyotp.TOTP(sec).now()
        t_a = time.time()
        resp_a = s.post("https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment",
                        headers=h6, data=json.dumps({"code": code6, "session_id": sid_,
                                                     "factor_id": fid, "factor_type": "totp"}), timeout=30)
        T.mark("activate")
        print(f"[OK] activate -> {resp_a.status_code} ({(time.time()-t_a):.1f}s): {resp_a.text[:150]}")
        resp_i = s.get("https://chatgpt.com/backend-api/accounts/mfa_info", headers=h6, timeout=30)
        mfa_on = '"mfa_enabled":true' in resp_i.text
        print(f"[OK] mfa_info mfa_enabled={mfa_on}")

        # 13. 统一落盘 accounts.jsonl(含 totp_secret/refresh_token/status) → 可测活
        from gptreg.account_store import save_account

        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                    "secure": bool(getattr(c, "secure", False))}
                   for c in s.session.cookies.jar]
        save_account(load_config(), record={
            "email": email,
            "password": password,
            "access_token": at,
            "refresh_token": info.get("refreshToken") or info.get("refresh_token") or "",
            "device_id": s.device_id,
            "name": "James Miller",
            "birthdate": "1998-05-12",
            "mail_main": base_email,
            "totp_secret": sec,
            "status": "ok",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sentinel_obs": {
                "challenge_mode": "quickjs_pwd_v3",
                "create_has_so": bool(so_b),
                "create_so_len": len(so_b or ""),
                "t_len": len(token),
                "totp_enrolled": True,
            },
            "session_cookies": cookies,
        })
        print("[落盘] 账号已保存到 accounts.jsonl(含 totp_secret)")

        print(f"\n账号: {email}\n密码: {password}\nTOTP: {sec}")
        T.dump(t0)
    finally:
        resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

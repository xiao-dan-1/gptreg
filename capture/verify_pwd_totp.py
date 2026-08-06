#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码V3注册 + 立即用 token 开 TOTP 2FA(纯协议, 无需 OAuth 授权)。

关键: verify_pwd_v3 注册后 access_token 带 recent_auth, 直接 POST mfa/enroll 即可拿 secret
(实测 200 返回 secret)。比 enable_totp_api 重新走 OAuth 简单且稳定。

流程: 注册(verify_pwd_v3) → 用注册 token+ookies 调 mfa/enroll → 输出 账号----密码----TOTP secret
记录分阶段耗时。

用法: python capture/verify_pwd_totp.py --email 主号 [--proxy ...]
"""
from __future__ import annotations

import json
import re
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
from gptreg.pipeline import _root  # noqa: E402

FLOW_PWD = "username_password_create"
FLOW_OAUTH = "oauth_create_account"
REGISTER_URL = "https://auth.openai.com/api/accounts/user/register"
PASSWORD_REFERER = "https://auth.openai.com/create-account/password"
ABOUT_YOU_REFERER = "https://auth.openai.com/about-you"


def _base(m: str) -> str:
    """取主号用户名：x+tag@dom → x；裸用户名(无 @)原样。统一返回用户名便于 --email 裸用户名匹配号池。"""
    return (m or "").split("@")[0].split("+")[0]


def _register(cfg, args, account, email, password, display_name, bday, base_email):
    """复用 verify_pwd_v3 注册逻辑, 返回 (access_token, device_id, cookies) 或 None。"""
    from gptreg.config import load_config
    resolved = resolve_proxy(cfg, override=args.proxy)
    session = BrowserSession(cfg, proxy=resolved.session_url)
    st = {"start": time.time()}
    try:
        auth.get_providers(session)
        time.sleep(0.3)
        csrf = auth.get_csrf_token(session)
        time.sleep(0.3)
        au = auth.signin_openai(session, csrf, email)
        time.sleep(0.3)
        auth.follow_authorize(session, au, attempts=1)
        time.sleep(0.5)

        # register(设密码)
        token, _ = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_PWD, cfg=cfg)
        headers = session.auth_api_headers(referer=PASSWORD_REFERER)
        headers["openai-sentinel-token"] = token
        resp = session.post(REGISTER_URL, headers=headers,
                            data=json.dumps({"username": email, "password": password}))
        if resp.status_code != 200:
            print(f"[register] 失败 {resp.status_code}: {resp.text[:150]}")
            return None
        reg = resp.json()

        # send_otp
        send_url = reg.get("continue_url") or "https://auth.openai.com/api/accounts/email-otp/send"
        r = session.get(send_url, headers=session.auth_navigate_headers(referer=PASSWORD_REFERER),
                        allow_redirects=True)
        print(f"[send_otp] HTTP {r.status_code} ({time.time()-st['start']:.1f}s)")

        # 收码(after_ts 用流程开始, 邮件可能在 authorize 就发; 超时自动重发, 最多 otp_max_attempts 次)
        # (2026-08-06 批量: 部分主号 IMAP 降级 Graph 有 ~150s 索引延迟, 单次 150s 收码超时→整批失败;
        #  超时重发一次可救回, 与 verify_pwd_v3 一致)
        otp_after = st["start"]
        client = build_mail_client(account, proxy=resolved.session_url or None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
        identity = mail_identity_key(account)
        cache_path = resolve_path(cfg.get("mail", {}).get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
        used_cache = UsedCodeCache(cache_path)
        exclude = used_cache.seen_codes(identity)
        mail_cfg = cfg.get("mail", {})
        otp_timeout = int(mail_cfg.get("otp_wait", 150) or 150)
        otp_max_attempts = max(1, int(mail_cfg.get("otp_max_attempts", 2) or 2))
        otp = None
        for attempt in range(otp_max_attempts):
            try:
                otp = client.wait_for_otp(after_ts=otp_after, timeout=otp_timeout,
                                          interval=3, settle_seconds=5, exclude_codes=exclude)
                break
            except Exception as exc:
                if attempt >= otp_max_attempts - 1:
                    raise
                print(f"  [OTP] 第{attempt+1}次收码失败({type(exc).__name__}: {str(exc)[:60]})，重发验证码...")
                time.sleep(1)
                r_retry = session.get(send_url,
                                      headers=session.auth_navigate_headers(referer=PASSWORD_REFERER),
                                      allow_redirects=True)
                print(f"  重发: HTTP {r_retry.status_code} 落点={str(getattr(r_retry, 'url', ''))[:50]}")
                otp_after = time.time()
        used_cache.remember(identity, otp, email=email, status="submitted")
        print(f"[OTP] 收到 {otp} ({time.time()-st['start']:.1f}s)")

        # validate_otp
        vr = auth.validate_email_otp(session, otp, None)

        # create_account(vm t + browser so)
        tok2, _ = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_OAUTH, cfg=cfg)
        so_b = None
        try:
            from gptreg.browser_sentinel import harvest_browser_sentinel
            br = harvest_browser_sentinel(cfg, flow=FLOW_OAUTH, device_id=session.device_id,
                                          proxy=resolved.session_url, headless=True, timeout_s=90)
            if br.get("ok") and br.get("so_header"):
                so_b = br["so_header"]
        except Exception:
            pass
        h2 = session.auth_api_headers(referer=ABOUT_YOU_REFERER)
        h2["openai-sentinel-token"] = tok2
        if so_b:
            h2["openai-sentinel-so-token"] = so_b
        resp2 = session.post("https://auth.openai.com/api/accounts/create_account",
                             headers=h2, data=json.dumps({"name": display_name, "birthdate": bday}))
        print(f"[create_account] HTTP {resp2.status_code} ({time.time()-st['start']:.1f}s)")
        if resp2.status_code != 200:
            return None

        # callback + session
        cr = resp2.json()
        cu = cr.get("continue_url") or cr.get("url")
        if not cu:
            print("[callback] 无 continue_url")
            return None
        auth.follow_oauth_callback(session, cu)
        info = auth.fetch_session(session)
        at = info.get("accessToken")
        if not at:
            print("[session] 无 accessToken")
            return None
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                    "secure": bool(getattr(c, "secure", False))}
                   for c in session.session.cookies.jar]
        print(f"[session] access_token 拿到 ({time.time()-st['start']:.1f}s)")
        return {
            "at": at,
            "device_id": session.device_id,
            "cookies": cookies,
            # 刷新凭证: OAuth offline_access scope 的 refreshToken(有则可无限刷新)
            "refresh_token": info.get("refreshToken") or info.get("refresh_token") or "",
            # sentinel 观测: create_account 用 quickjs t + browser so
            "t_len": len(tok2),
            "so_len": len(so_b or ""),
            "has_so": bool(so_b),
        }
    finally:
        resolved.close()


def main() -> int:
    import argparse as _ap
    import random as _r
    import string as _s

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--alias", action="store_true")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    t0 = time.time()

    # 号池选主号
    account = None
    for line in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
        a = parse_mail_line(line.strip())
        if not a:
            continue
        if args.email and _base(a["email"]) != _base(args.email):
            continue
        account = a
        break
    if not account:
        print("号池找不到收码账号")
        return 1
    base_email = account["email"]
    if args.alias:
        name, dom = base_email.split("@")
        tag = "".join(_r.choice(_s.ascii_lowercase + _s.digits) for _ in range(6))
        email = f"{name}+{tag}@{dom}"
    else:
        email = base_email
    password = "".join(_r.choice(_s.ascii_letters + _s.digits + "!@#$%") for _ in range(14))
    display_name, bday = "James Miller", "1998-05-12"
    print(f"注册邮箱: {email}  密码: {password}")

    # 1. 注册
    t1 = time.time()
    reg = _register(cfg, args, account, email, password, display_name, bday, base_email)
    print(f"[阶段1 注册] {(time.time()-t1):.1f}s")
    if not reg:
        print("[x] 注册失败")
        return 2

    # 1.5 落盘延后到 enroll+secret 后统一写入(含 totp_secret/refresh_token/status)

    # 2. 立即 mfa/enroll
    t2 = time.time()
    resolved = resolve_proxy(cfg, override=args.proxy)
    s = BrowserSession(cfg, proxy=resolved.session_url)
    s.device_id = reg["device_id"]
    for c in reg["cookies"]:
        for dom in (c["domain"].lstrip("."), c["domain"]):
            if dom:
                try:
                    s.session.cookies.set(c["name"], c["value"], domain=dom, path=c["path"])
                except Exception:
                    pass
    h6 = s.chatgpt_headers(referer="https://chatgpt.com/")
    h6["authorization"] = f"Bearer {reg['at']}"
    h6["oai-device-id"] = reg["device_id"]
    h6.pop("content-type", None)
    h6["content-type"] = "application/json"
    resp_enroll = s.post("https://chatgpt.com/backend-api/accounts/mfa/enroll",
                         headers=h6, data=json.dumps({"factor_type": "totp"}), timeout=30)
    print(f"[mfa/enroll] HTTP {resp_enroll.status_code} ({time.time()-t2:.1f}s)")
    if resp_enroll.status_code != 200:
        resolved.close()
        print(f"[x] enroll 失败: {resp_enroll.text[:200]}")
        return 3

    # 2.5 activate_enrollment: 用 pyotp 码确认, 让 2FA 真正激活
    # (2026-08-06 实证: 只 enroll 不 activate → mfa_enabled 仍 false, 登录不要求 TOTP;
    #  activate 后 mfa_enabled:true, password/verify 进入 mfa_challenge。enroll→activate 是必选链)
    ej = resp_enroll.json()
    enroll_secret = str(ej.get("secret") or "")
    session_id = ej.get("session_id")
    factor_id = (ej.get("factor") or {}).get("id")
    print(f"[enroll] secret={enroll_secret[:10]}... session_id={str(session_id)[:16]} factor_id={str(factor_id)[:16]}")
    if enroll_secret and session_id and factor_id:
        import pyotp
        code6 = pyotp.TOTP(enroll_secret).now()
        resp_act = s.post("https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment",
                          headers=h6, data=json.dumps({
                              "code": code6, "session_id": session_id,
                              "factor_id": factor_id, "factor_type": "totp"}), timeout=30)
        print(f"[activate_enrollment] HTTP {resp_act.status_code}: {resp_act.text[:200]} ({time.time()-t2:.1f}s)")
        try:
            resp_info = s.get("https://chatgpt.com/backend-api/accounts/mfa_info", headers=h6, timeout=30)
            mfa_on = '"mfa_enabled":true' in resp_info.text
            print(f"[mfa_info] mfa_enabled={mfa_on} {(resp_info.text or '')[:120]}")
        except Exception:
            pass
    else:
        print("[warn] enroll 响应缺 session_id/factor_id, 跳过 activate")
    resolved.close()

    # 3. 提取 secret(优先 enroll 响应的 secret 字段, 兜底 regex)
    txt = resp_enroll.text
    secret = enroll_secret
    if not secret:
        m_otp = re.search(r"otpauth://[^\s\"']+", txt)
        m_sec = re.search(r"[A-Z2-7]{32}", txt)
        if m_otp:
            secret = m_otp.group(0)
            m2 = re.search(r"[?&]secret=([A-Z2-7]+)", secret)
            if m2:
                secret = m2.group(1)
        elif m_sec:
            secret = m_sec.group(0)
    if not secret:
        print(f"[x] 未提取到 secret: {txt[:300]}")
        return 4

    # 3.5 统一落盘 accounts.jsonl 主库(含 totp_secret/refresh_token/status)
    from gptreg.store import save_account

    save_account(cfg, record={
        "email": email,
        "password": password,
        "access_token": reg["at"],
        "refresh_token": reg["refresh_token"],
        "device_id": reg["device_id"],
        "name": display_name,
        "birthdate": bday,
        "mail_main": base_email,
        "totp_secret": secret,
        "status": "ok",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sentinel_obs": {
            "challenge_mode": "quickjs_pwd_v3",
            "create_has_so": reg["has_so"],
            "create_so_len": reg["so_len"],
            "t_len": reg["t_len"],
            "flow": FLOW_PWD,
            "create_flow": FLOW_OAUTH,
            "totp_enrolled": True,
        },
        "session_cookies": reg["cookies"],
    })
    print("[落盘] 账号已保存到 accounts.jsonl(含 totp_secret)")

    print("\n" + "=" * 50)
    print(f"账号: {email}")
    print(f"密码: {password}")
    print(f"TOTP: {secret}")
    print(f"otpauth: otpauth://totp/ChatGPT:{email}?secret={secret}&issuer=ChatGPT")
    print("=" * 50)
    print(f"[总耗时] {(time.time()-t0):.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

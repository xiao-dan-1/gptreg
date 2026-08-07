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

from gptreg.config import (  # noqa: E402
    load_config,
    random_birthdate,
    random_display_name,
    resolve_path,
)
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


class RegisterBlocked(RuntimeError):
    """register 400 invalid_auth_step——出口 IP 被 OpenAI 风控, 换 sid(新出口)可重试。"""
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
        final = auth.follow_authorize(session, au, attempts=1)
        time.sleep(0.5)

        # register(设密码)
        token, _ = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_PWD, cfg=cfg)
        headers = session.auth_api_headers(referer=PASSWORD_REFERER)
        headers["openai-sentinel-token"] = token
        resp = session.post(REGISTER_URL, headers=headers,
                            data=json.dumps({"username": email, "password": password}))
        if resp.status_code != 200:
            land = final or "?"
            if "email-verification" in land:
                _diag = "email-verification → 主号可能已注册, 需用 plus 别名注册"
            elif "log-in" in land or "/login" in land:
                _diag = "log-in → 主号已注册(登录流程), register 不合法"
            elif "create-account" in land:
                _diag = "create-account/password → 未注册, 仍 400 多为出口 IP 信誉"
            else:
                _diag = land[:60]
            print(f"[register] 失败 {resp.status_code}: {resp.text[:150]}")
            print(f"[register/诊断] authorize 落点: {_diag}")
            raise RegisterBlocked(f"register HTTP {resp.status_code}: {resp.text[:150]}")
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

        # create_account: quickjs t 与 browser so 并行采集。
        # 两路独立资源(Node 进程产 t / Chrome 采 so), 串行=等待慢者浪费 ~10-15s
        import threading as _th

        _holder: dict[str, object] = {}

        def _gen_t() -> None:
            _ct = time.time()
            try:
                tok, _ = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_OAUTH, cfg=cfg)
                _holder["tok2"] = tok
            except Exception as exc:
                print(f"[x] quickjs t 生成失败: {type(exc).__name__}: {str(exc)[:120]}")
                _holder["t_err"] = f"{type(exc).__name__}: {exc}"
            _holder["t_s"] = time.time() - _ct

        def _gen_so() -> None:
            _ct = time.time()
            so = None
            try:
                from gptreg.browser_sentinel import harvest_browser_sentinel
                br = harvest_browser_sentinel(cfg, flow=FLOW_OAUTH, device_id=session.device_id,
                                              proxy=resolved.session_url, headless=True, timeout_s=90)
                if br.get("ok") and br.get("so_header"):
                    so = br["so_header"]
                else:
                    print(f"[warn] browser so 采集未成功: {str(br.get('error') or 'empty so')[:100]} (create 将无 so)")
            except Exception as exc:
                print(f"[warn] browser so 采集异常: {type(exc).__name__}: {str(exc)[:100]} (create 将无 so)")
            _holder["so_b"] = so
            _holder["so_s"] = time.time() - _ct

        _ct0 = time.time()
        _th_t = _th.Thread(target=_gen_t)
        _th_so = _th.Thread(target=_gen_so)
        _th_t.start()
        _th_so.start()
        _th_t.join()
        _th_so.join()
        tok2 = str(_holder.get("tok2") or "")
        so_b = _holder.get("so_b")
        print(f"[create/timing] quickjs t={_holder.get('t_s', 0):.1f}s so={_holder.get('so_s', 0):.1f}s 并行总={time.time()-_ct0:.1f}s")
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
            # 本次注册出口代理(落盘用, 便于事后归因 IP 风控 vs 基建)
            "proxy_used": resolved.upstream_url or resolved.session_url or "",
        }
    finally:
        resolved.close()


def main() -> int:
    import argparse as _ap
    import random as _r
    import string as _s

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--alias", action="store_true", help="强制用 plus 别名注册(默认走 config mail.use_alias)")
    ap.add_argument("--no-alias", action="store_true", help="禁用别名, 用主号直接注册")
    ap.add_argument("--proxy", default=None, help="覆盖代理(默认走 config 动态链式, 勿用 10808 僵尸端口)")
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
    # 默认 plus 别名注册(config mail.use_alias=true)——号池主号很多已在 OpenAI 注册,
    # 用主号直接注册会落 email-verification/log-in → register 400 invalid_auth_step;
    # 别名(主号+随机tag)是全新邮箱, register 直接过(已实证)
    use_alias = bool(cfg.get("mail", {}).get("use_alias", True))
    if args.no_alias:
        use_alias = False
    elif args.alias:
        use_alias = True
    if use_alias:
        name, dom = base_email.split("@")
        tag = "".join(_r.choice(_s.ascii_lowercase + _s.digits) for _ in range(6))
        email = f"{name}+{tag}@{dom}"
    else:
        email = base_email
    password = "".join(_r.choice(_s.ascii_letters + _s.digits + "!@#$%") for _ in range(14))
    display_name = random_display_name()
    bday = random_birthdate(cfg)
    print(f"注册邮箱: {email} (主号: {base_email}, {'别名' if use_alias else '直接用主号'})  密码: {password}")
    print(f"注册身份: {display_name} / {bday}")

    # 1. 注册(register 400 IP 风控时自动换 sid 重试——单号自愈, 命中干净住宅 IP 即成功)
    import re as _re

    if not args.proxy:
        # 默认走动态模板(cliproxy), 便于 register 400 时换 sid 自动重试
        from gptreg.proxyutil import build_dynamic_proxy

        args.proxy = build_dynamic_proxy(cfg)
    t1 = time.time()
    reg = None
    for _att in range(3):
        try:
            reg = _register(cfg, args, account, email, password, display_name, bday, base_email)
            break
        except RegisterBlocked as _rb:
            print(f"[warn] register 被拒(IP 风控?): {str(_rb)[:80]}")
            if _att >= 2 or "-sid-" not in (args.proxy or "") or "-t-" not in (args.proxy or ""):
                print("[x] 注册失败(IP 风控, 无法换 sid 或已达上限)")
                reg = None
                break
            _new_sid = "".join(_r.choice(_s.ascii_lowercase + _s.digits) for _ in range(8))
            args.proxy = _re.sub(r"-sid-[^-]+-t-", f"-sid-{_new_sid}-t-", args.proxy, count=1)
            print(f"[retry] 换新 sid 重试 ({_att+2}/3)")
            time.sleep(1)
    print(f"[阶段1 注册] {(time.time()-t1):.1f}s")
    if not reg:
        print("[x] 注册失败")
        return 2

    # 1.5 落盘延后到 enroll+secret 后统一写入(含 totp_secret/refresh_token/status)

    # 2. 立即 mfa/enroll
    # 注册成功但 2FA 未开的账号也先落盘(防白建丢凭据; status=registered_no_totp 待补 2FA)
    def _save_partial(status: str) -> None:
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
            "status": status,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session_cookies": reg["cookies"],
            "proxy_used": reg.get("proxy_used", ""),
        })
        print(f"[落盘] 已保存注册凭据(status={status})")

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
        _save_partial("registered_no_totp")
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
        _save_partial("registered_no_totp")
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
        "proxy_used": reg.get("proxy_used", ""),
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

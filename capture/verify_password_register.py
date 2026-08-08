#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码模式注册验证:POST /api/accounts/user/register + username_password_create 的 quickjs t(不传 so)。

背景:username_password_create flow 实测(2026-08-05)不要求 so(collector_dx/snapshot_dx=0)。
若注册成功且存活 → 纯协议(无浏览器)正式复活,绕过整个 vm so 死局。

复用现有链:providers → csrf → signin → authorize → OTP validate → quickjs t → register(password)。
消耗 1 个号池邮箱 + 1 次 OTP。

用法: python capture/verify_password_register.py
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

from gptreg.config import load_config, resolve_path  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402
from gptreg.mail.pool import parse_mail_line, choose_registration_email  # noqa: E402
from gptreg.mail.providers import build_mail_client, mail_identity_key, UsedCodeCache  # noqa: E402
from gptreg.register_otp import _root  # noqa: E402

FLOW_PWD = "username_password_create"
REGISTER_URL = "https://auth.openai.com/api/accounts/user/register"
PASSWORD_REFERER = "https://auth.openai.com/create-account/password"


def random_password(length: int = 16) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(length))


def _warm_password_page(session: BrowserSession, with_callback: bool = True) -> dict:
    """访问 create-account/password 页推进 auth step(防 invalid_auth_step)。

    返回诊断信息(status/落点/session-ended)。
    """
    out: dict = {}
    try:
        h = session.auth_navigate_headers(referer="https://auth.openai.com/email-verification")
        h["sec-fetch-site"] = "same-origin"
        resp = session.get(
            "https://auth.openai.com/create-account/password", headers=h, allow_redirects=True,
        )
        text = resp.text or ""
        out["status"] = resp.status_code
        out["url"] = str(getattr(resp, "url", ""))
        out["session_ended"] = "session has ended" in text.lower() or "session-ended" in text.lower()
        out["text_head"] = text[:120].replace("\n", " ")
    except Exception as exc:
        out["error"] = str(exc)
    if with_callback:
        try:
            cb = session.auth_navigate_headers(referer="https://auth.openai.com/email-verification")
            cb["sec-fetch-site"] = "same-origin"
            session.get(
                "https://auth.openai.com/api/accounts/authorize/callback",
                headers=cb, allow_redirects=True,
            )
        except Exception:
            pass
    return out


def _base(m: str) -> str:
    """归一化主邮箱:去掉 +alias。"""
    return m.split("@")[0].split("+")[0] + "@" + m.split("@")[1]


def pick_free_account(cfg: dict, *, force_email: str = "") -> tuple[dict, str]:
    """挑一个主邮箱从未注册过(accounts.jsonl + 号池 state)的号。密码模式 username 必须全局唯一。

    返回 (account, 注册用 email)。
    """
    taken: set[str] = set()
    # 号池 state.used
    st_path = ROOT / "mail_pool.txt.state.json"
    if st_path.exists():
        try:
            for u in json.loads(st_path.read_text(encoding="utf-8")).get("used") or []:
                if isinstance(u, str):
                    taken.add(_base(u).lower())
        except Exception:
            pass
    # accounts.jsonl 所有已成功账号
    acc_path = ROOT / "output" / "accounts.jsonl"
    if acc_path.exists():
        for line in acc_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("email"):
                    taken.add(_base(d["email"]).lower())
            except Exception:
                pass

    pool_file = str((cfg.get("mail") or {}).get("pool_file") or "mail_pool.txt")
    candidates = []
    for line in Path(pool_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        a = parse_mail_line(line)
        if not a:
            continue
        m = a["email"]
        if force_email and _base(m).lower() == _base(force_email).lower():
            return a, m
        if _base(m).lower() in taken:
            continue
        candidates.append(a)
    if not candidates:
        raise RuntimeError("号池无可用的未注册邮箱")
    account = candidates[0]
    email, _ = choose_registration_email(account, cfg)
    return account, email


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="", help="指定注册邮箱(主号)")
    ap.add_argument("--proxy", default="", help="覆盖代理,如 http://127.0.0.1:10808")
    args = ap.parse_args()
    force_email, proxy_override = args.email, args.proxy
    cfg = load_config()
    resolved = resolve_proxy(cfg, override=proxy_override or None)
    session = BrowserSession(cfg, proxy=resolved.session_url)
    session._proxy_label = resolved.label()
    print(f"代理: {resolved.label()}")

    # 1. 挑未注册邮箱
    account, email = pick_free_account(cfg, force_email=force_email)
    password = random_password()
    print(f"邮箱: {email} (主 {account['email']})  密码: {password}")

    # 2. OAuth 登录发验证码
    print("\n[1/6] OAuth 登录...")
    auth.get_providers(session)
    time.sleep(0.3)
    csrf = auth.get_csrf_token(session)
    time.sleep(0.3)
    authorize_url = auth.signin_openai(session, csrf, email)
    otp_after = time.time()
    time.sleep(0.3)
    auth.follow_authorize(session, authorize_url)
    time.sleep(1.5)

    # 3. OTP 阶段 sentinel(pow,与现有 pipeline 一致)
    print("[2/6] OTP sentinel(pow)...")
    sentinel_otp, _ = auth.make_sentinel_headers(session, None, "authorize_continue", source="pow")

    # 4. 收验证码
    print("[3/6] 等待验证码...")
    mail_cfg = cfg.get("mail", {})
    browser = cfg.get("browser", {})
    client = build_mail_client(
        account,
        proxy=resolved.session_url or None,
        impersonate=browser.get("impersonate", "chrome142"),
    )
    identity = mail_identity_key(account)
    cache_path = resolve_path(mail_cfg.get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
    used_cache = UsedCodeCache(cache_path)
    exclude = used_cache.seen_codes(identity)
    otp = client.wait_for_otp(
        after_ts=otp_after,
        timeout=max(int(mail_cfg.get("max_wait", 90)), 180),
        interval=int(mail_cfg.get("poll_interval", 3)),
        settle_seconds=int(mail_cfg.get("settle_seconds", 5)),
        exclude_codes=exclude,
    )
    used_cache.remember(identity, otp, email=email, status="submitted")
    print(f"验证码: {otp}")

    # 5. validate OTP
    print("[4/6] validate OTP...")
    validate_result = auth.validate_email_otp(session, otp, sentinel_otp)
    print(f"  validate: {str(validate_result)[:200]}")
    auth.maybe_follow_external(session, validate_result)
    time.sleep(0.5)
    # 密码模式:主动 warm create-account/password 页,把 auth step 推进到 password 步骤
    warm_diag = _warm_password_page(session)
    print(f"  warm password 页诊断: {str(warm_diag)[:220]}")
    # session 关键 cookie
    _ck = {c.name for c in session.session.cookies.jar}
    print(f"  session cookies: {sorted(x for x in _ck if 'auth' in x.lower() or 'session' in x.lower())[:10]}")

    # 6. quickjs 产 username_password_create 的 t(不要求 so,不传 so)
    print("[5/6] quickjs 产 t (username_password_create)...")
    token, so_header = get_sentinel_token_via_quickjs(
        session, session.device_id, flow=FLOW_PWD, cfg=cfg, timeout_ms=120000,
    )
    print(f"  t_len={len(token)} so={so_header}")

    # 7. POST /api/accounts/user/register (invalid_auth_step 时 warm password 页重试)
    print("[6/6] POST register(password)...")
    headers = session.auth_api_headers(referer=PASSWORD_REFERER)
    headers["openai-sentinel-token"] = token
    body = json.dumps({"password": password, "username": email})

    resp = None
    for attempt in range(3):
        resp = session.post(REGISTER_URL, headers=headers, data=body)
        text = resp.text or ""
        print(f"  尝试 {attempt + 1}: HTTP {resp.status_code}: {text[:150]}")
        if resp.status_code == 400 and "invalid_auth_step" in text:
            print("  invalid_auth_step → warm password 页后重试")
            _warm_password_page(session)
            time.sleep(0.6)
            continue
        break
    try:
        reg = resp.json()
    except Exception:
        reg = {"status": resp.status_code, "text": (resp.text or "")[:200]}
    if resp.status_code != 200:
        print(f"\n[x] register 失败: {str(reg)[:300]}")
        return 2

    continue_url = reg.get("continue_url") or ""
    if continue_url:
        print(f"  continue_url: {continue_url[:120]}")
        auth.follow_oauth_callback(session, continue_url)
        session_info = auth.fetch_session(session)
        access_token = session_info.get("accessToken")
        print(f"  access_token: {str(access_token)[:30]}...")
        health = auth.check_account_health(session, access_token)
        print(f"\n健康检查: {health.get('status')} {(str(health.get('body') or health.get('detail')) or '')[:80]}")
    else:
        print(f"  [warn] 无 continue_url,register 返回: {str(reg)[:200]}")

    resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

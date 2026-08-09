"""backfill: 给 accounts.jsonl 中「有 totp_secret 但无 access_token」的账号补 token。

从 capture/tools/backfill_token.py 收编, 修复写回非原子(原 open("w") 整写 →
逐账号 account_store.save_account 原子 upsert + 自动备份)。
登录链: signin → authorize/continue → password/verify → mfa_challenge
→ mfa/verify{type:totp,id,code} → callback → access_token。
"""
from __future__ import annotations

import json
import time
from typing import Any

from gptreg import auth
from gptreg.account_store import load_accounts, save_account
from gptreg.proxyutil import build_dynamic_proxy, resolve_proxy
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs
from gptreg.session import BrowserSession, jar_to_list

ISSUER = "https://auth.openai.com"


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("backfill", help="补缺失 access_token(密码+TOTP 登录)")
    p.add_argument("--emails", default="", help="逗号分隔,默认全部缺 token 的 TOTP 账号")
    p.add_argument("--proxy", default="", help="留空=config 动态链式")
    p.set_defaults(func=run)


def _api_headers(s, referer: str) -> dict:
    h = s.auth_api_headers(referer=referer)
    h.pop("content-type", None)
    h["content-type"] = "application/json"
    return h


def login_get_token(cfg, proxy_url: str, email: str, password: str, totp_secret: str):
    """密码+TOTP 登录 → ((at, device_id, cookies) 或 None, reason)。"""
    r = resolve_proxy(cfg, override=proxy_url)
    s = BrowserSession(cfg, proxy=r.session_url)
    try:
        auth.get_providers(s)
        time.sleep(0.3)
        csrf = auth.get_csrf_token(s)
        time.sleep(0.3)
        au = auth.signin_openai(s, csrf, email)
        time.sleep(0.3)
        auth.follow_authorize(s, au, attempts=1)
        time.sleep(0.3)

        tok_ac, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="authorize_continue", cfg=cfg)
        h = _api_headers(s, f"{ISSUER}/log-in")
        h["openai-sentinel-token"] = tok_ac
        r2 = s.post(f"{ISSUER}/api/accounts/authorize/continue",
                    headers=h, data=json.dumps({"username": {"kind": "email", "value": email}}),
                    allow_redirects=False, timeout=30)
        if r2.status_code != 200:
            return None, f"authorize/continue HTTP {r2.status_code}(会话/邮箱问题)"

        tok_pw, _ = get_sentinel_token_via_quickjs(s, s.device_id, flow="password_verify", cfg=cfg)
        h = _api_headers(s, f"{ISSUER}/log-in/password")
        h["openai-sentinel-token"] = tok_pw
        r3 = s.post(f"{ISSUER}/api/accounts/password/verify",
                    headers=h, data=json.dumps({"password": password}),
                    allow_redirects=False, timeout=30)
        if r3.status_code != 200:
            return None, f"password/verify HTTP {r3.status_code}(密码可能错误/账号状态)"
        c3 = r3.json()
        factor_id = (c3.get("page") or {}).get("payload", {}).get("factor_id")
        if not factor_id:
            return None, "账号 2FA 未激活(无 TOTP factor), 需重新 enroll+activate"

        import pyotp

        code6 = pyotp.TOTP(totp_secret).now()
        r4 = s.post(f"{ISSUER}/api/accounts/mfa/verify",
                    headers=_api_headers(s, f"{ISSUER}/log-in/password"),
                    data=json.dumps({"type": "totp", "id": factor_id, "code": code6}),
                    allow_redirects=False, timeout=30)
        if r4.status_code != 200:
            return None, f"mfa/verify HTTP {r4.status_code}: {r4.text[:60]}"
        cont = r4.json().get("continue_url") or ""
        if not cont:
            return None, "mfa/verify 无 continue_url"
        auth.follow_oauth_callback(s, cont)
        info = auth.fetch_session(s)
        at = info.get("accessToken")
        if not at:
            return None, "无 accessToken"
        cookies = jar_to_list(s)
        return (at, s.device_id, cookies), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"
    finally:
        r.close()


def run(cfg: dict[str, Any], args) -> int:
    recs = load_accounts(cfg)

    want = {e.strip() for e in args.emails.split(",") if e.strip()}
    targets = [d for d in recs if d.get("totp_secret") and not d.get("access_token")
               and (not want or d.get("email") in want)]
    if not targets:
        print("没有需要补 token 的账号")
        return 0
    print(f"待补 token: {len(targets)} 个")
    for d in targets:
        print(f"  {d.get('email')}")

    ok = 0
    for i, d in enumerate(targets, 1):
        # 每次换新 sid(独立出口 IP, 避免连续登录限流)
        proxy = build_dynamic_proxy(cfg) if not args.proxy else args.proxy
        email, password, secret = d["email"], d.get("password", ""), d["totp_secret"]
        print(f"\n[{i}/{len(targets)}] 登录 {email} ...")
        got, reason = login_get_token(cfg, proxy or None, email, password, secret)
        if not got:
            print(f"  [x] 补 token 失败: {reason}")
            time.sleep(3)
            continue
        at, did, cookies = got
        d["access_token"] = at
        d["device_id"] = did
        d["session_cookies"] = cookies
        d["refresh_token"] = d.get("refresh_token") or ""
        d["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not d.get("sentinel_obs"):
            d["sentinel_obs"] = {"challenge_mode": "quickjs_pwd_v3", "totp_enrolled": True}
        ok += 1
        print(f"  [OK] at 前20: {at[:20]}... cookies={len(cookies)}")
        # 逐账号原子 upsert(替代原整写, 防并发截断), 自动备份
        try:
            save_account(cfg, record=d)
        except Exception as exc:
            print(f"      [回写失败] {type(exc).__name__}: {str(exc)[:60]}")
        time.sleep(3)

    print(f"\n补齐 {ok}/{len(targets)} 个 token")
    return 0

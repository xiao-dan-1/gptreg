#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给 accounts.jsonl 中「有 totp_secret 但无 access_token」的账号补 token。

登录链(密码 + TOTP): signin → authorize/continue → password/verify → mfa_challenge
→ mfa/verify{type:totp,id,code} → callback → access_token。更新 accounts.jsonl 记录。

用法: python capture/backfill_token.py [--emails 逗号分隔,默认全部缺 token 的] [--proxy 动态]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402

ACC = ROOT / "output" / "accounts.jsonl"
ISSUER = "https://auth.openai.com"


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
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                    "secure": bool(getattr(c, "secure", False))}
                   for c in s.session.cookies.jar]
        return (at, s.device_id, cookies), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"
    finally:
        r.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emails", default="", help="逗号分隔,默认全部缺 token 的 TOTP 账号")
    ap.add_argument("--proxy", default="", help="留空=config 动态链式")
    args = ap.parse_args()

    cfg = load_config()
    recs = [json.loads(l) for l in ACC.read_text(encoding="utf-8").splitlines() if l.strip()]

    want = {e.strip() for e in args.emails.split(",") if e.strip()}
    targets = [d for d in recs if d.get("totp_secret") and not d.get("access_token")
               and (not want or d.get("email") in want)]
    if not targets:
        print("没有需要补 token 的账号")
        return 0
    print(f"待补 token: {len(targets)} 个")
    for d in targets:
        print(f"  {d.get('email')}")

    # 动态代理(每次换 IP, 避免连续登录限流)
    import re as _re, random as _rnd, string as _str
    tpl = ((cfg.get("proxy") or {}).get("dynamic") or {}).get("template") or ""
    ok = 0
    for i, d in enumerate(targets, 1):
        proxy = ""
        if tpl:
            sid = "".join(_rnd.choices(_str.ascii_lowercase + _str.digits, k=8))
            proxy = _re.sub(r"-sid-[a-zA-Z0-9]+-t-", f"-sid-{sid}-t-", tpl)
        else:
            proxy = args.proxy
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
        time.sleep(3)

    # 写回
    with ACC.open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n补齐 {ok}/{len(targets)} 个 token")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

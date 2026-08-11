"""relogin: password+TOTP 纯 HTTP 重登 → 换新 access_token(账号续命兜底)。

背景: access_token(JWT) 10 天过期; refresh 命令用 session_cookies 重抓续期,
但 session_cookies 本身会失效(无 refresh_token, 依赖存量 cookie)。
relogin 提供独立的根本途径: 用 password+TOTP 完整重登, 不依赖任何存量凭据外的 cookie。

关键突破(2026-08-12 研究): signin/openai **必须去掉 ext-passkey-client-capabilities=1111**
(它导向 passkey 分支 → 403)。去掉后 chatgpt 原生 NextAuth 链全通:
  signin → authorize → login_password → password/verify → mfa/verify(TOTP)
  → continue_url 直接是 chatgpt callback?code=ac_... → GET callback 完成授权
  → fetch_session 拿新 access_token + 新 session_cookies。

对比(同一 password+TOTP 账号实测):
  Codex 客户端 OAuth → mfa/verify 后强制 add_phone(手机验证, 结构性墙)
  chatgpt 客户端 raw OAuth → 能拿 code 但 /oauth/token 302 token_exchange_user_error(服务端持 client_secret)
  chatgpt 原生 signin(去 passkey) → ✅ 全通

用法: python main.py relogin [--email a,b] [--limit N] [--proxy URL] [--dry-run]
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from gptreg.account_store import load_accounts, update_account_tokens
from gptreg.commands.common import resolve_proxy_arg
from gptreg.proxyutil import resolve_proxy
from gptreg.session import BrowserSession, jar_to_list

from gptreg import auth


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("relogin", help="password+TOTP 纯 HTTP 重登(换新 access_token, 续命兜底)")
    p.add_argument("--email", default="", help="逗号分隔指定邮箱(默认所有有 password+TOTP 的账号)")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--proxy", default=None, help="覆盖代理；传 empty/none/direct 表示直连")
    p.add_argument("--dry-run", action="store_true", help="只走完登录链但不回写")
    p.set_defaults(func=run)


def _pkce():
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _jar_items(sess):
    try:
        return list(sess.session.cookies.jar)
    except Exception:
        return []


def _relogin_one(cfg: dict[str, Any], d: dict, proxy: str, dry_run: bool = False) -> dict:
    """password+TOTP 重登单账号 → 新 access_token + session_cookies。"""
    email = d.get("email", "?")
    password = d.get("password") or ""
    secret = d.get("totp_secret") or ""
    if not password or not secret:
        return {"email": email, "ok": False, "error": "无 password/totp_secret"}

    sess = BrowserSession(cfg, proxy=proxy)
    sess.device_id = d.get("device_id") or str(uuid.uuid4())
    nav = sess.auth_navigate_headers(referer="https://chatgpt.com/")
    try:
        # 1. signin 链(去 passkey flag —— 关键突破)
        auth.get_providers(sess)
        csrf = auth.get_csrf_token(sess)
        from urllib.parse import urlencode

        q = {"prompt": "login", "ext-oai-did": sess.device_id,
             "auth_session_logging_id": sess.auth_session_logging_id,
             "screen_hint": "login_or_signup", "login_hint": email}
        url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(q)
        h = sess.chatgpt_headers()
        h["content-type"] = "application/x-www-form-urlencoded"
        h["origin"] = "https://chatgpt.com"
        rsp = sess.post(url, headers=h,
                        data=urlencode({"callbackUrl": "https://chatgpt.com/", "csrfToken": csrf, "json": "true"}),
                        allow_redirects=False, timeout=30)
        if rsp.status_code != 200:
            return {"email": email, "ok": False, "error": f"signin HTTP {rsp.status_code}: {(rsp.text or '')[:80]}"}
        authorize_url = (rsp.json() or {}).get("url", "")
        if not authorize_url:
            return {"email": email, "ok": False, "error": "signin 无 authorize url"}

        # 2. follow authorize(落到 log-in/password)
        cur = authorize_url
        for _ in range(8):
            rr = sess.get(cur, headers=nav, allow_redirects=False, timeout=30)
            loc = rr.headers.get("location", "")
            if rr.status_code in (301, 302, 303, 307, 308) and loc:
                cur = loc if loc.startswith("http") else ("https://auth.openai.com" + (loc if loc.startswith("/") else "/" + loc))
                continue
            break

        # 3. authorize/continue(邮箱, pow sentinel)
        def _api(referer):
            hh = sess.auth_api_headers(referer=referer)
            hh["content-type"] = "application/json"
            return hh

        tok_ac, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
        h2 = _api("https://auth.openai.com/log-in")
        h2["openai-sentinel-token"] = tok_ac
        r2 = sess.post("https://auth.openai.com/api/accounts/authorize/continue",
                       headers=h2, data=json.dumps({"username": {"kind": "email", "value": email}}),
                       allow_redirects=False, timeout=30)
        if r2.status_code != 200:
            return {"email": email, "ok": False, "error": f"authorize/continue HTTP {r2.status_code}: {(r2.text or '')[:80]}"}
        c2 = r2.json()
        page_type = (c2.get("page") or {}).get("type", "")
        continue_url = c2.get("continue_url", "")

        # 4. password/verify(quickjs sentinel)
        from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs

        tok_pw, _ = get_sentinel_token_via_quickjs(sess, sess.device_id, flow="password_verify", cfg=cfg)
        h3 = _api("https://auth.openai.com/log-in/password")
        h3["openai-sentinel-token"] = tok_pw
        r3 = sess.post("https://auth.openai.com/api/accounts/password/verify",
                       headers=h3, data=json.dumps({"password": password}),
                       allow_redirects=False, timeout=30)
        if r3.status_code != 200:
            return {"email": email, "ok": False, "error": f"password/verify HTTP {r3.status_code}: {(r3.text or '')[:80]}"}
        c3 = r3.json()
        page_type = (c3.get("page") or {}).get("type", "") or page_type
        continue_url = c3.get("continue_url", "") or continue_url
        try:
            factor_id = (c3.get("page") or {}).get("payload", {}).get("factor_id")
        except Exception:
            factor_id = None

        # 5. mfa/verify(TOTP)
        if "mfa" in page_type.lower() or "mfa" in (continue_url or ""):
            import pyotp

            code = pyotp.TOTP(secret).now()
            h4 = _api("https://auth.openai.com/log-in/password")
            r4 = sess.post("https://auth.openai.com/api/accounts/mfa/verify",
                           headers=h4, data=json.dumps({"type": "totp", "id": factor_id, "code": code}),
                           allow_redirects=False, timeout=30)
            if r4.status_code != 200:
                return {"email": email, "ok": False, "error": f"mfa/verify HTTP {r4.status_code}: {(r4.text or '')[:80]}"}
            c4 = r4.json()
            continue_url = c4.get("continue_url", "") or continue_url
            page_type = (c4.get("page") or {}).get("type", "") or page_type

        # 6. GET chatgpt callback(完成 NextAuth 授权, 种 cookie)
        if "chatgpt.com/api/auth/callback" not in (continue_url or ""):
            return {"email": email, "ok": False, "error": f"continue_url 非 chatgpt callback: {str(continue_url)[:80]} page={page_type}"}
        sess.get(continue_url, headers=nav, allow_redirects=True, timeout=30)

        # 7. fetch_session → 新 access_token
        info = auth.fetch_session(sess)
        at = info.get("accessToken", "")
        if not at:
            return {"email": email, "ok": False, "error": "fetch_session 无 accessToken"}
        cookies = jar_to_list(sess)
        return {"email": email, "ok": True, "access_token": at,
                "session_token": info.get("sessionToken", ""),
                "session_cookies": cookies, "expires": info.get("expires", "")}
    except Exception as exc:
        return {"email": email, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:100]}"}
    finally:
        sess.close()


def run(cfg: dict[str, Any], args) -> int:
    accounts = [d for d in load_accounts(cfg) if d.get("password") and d.get("totp_secret")]
    if args.email:
        emails = {e.strip() for e in args.email.split(",") if e.strip()}
        accounts = [d for d in accounts if d.get("email") in emails]
    if args.limit:
        accounts = accounts[: args.limit]
    if not accounts:
        print("没有可重登的账号(需 password + totp_secret)")
        return 1
    print(f"重登 {len(accounts)} 个账号{' (DRY-RUN 不回写)' if args.dry_run else ''}:")

    resolved = resolve_proxy(cfg, override=resolve_proxy_arg(args))
    results = []
    try:
        for i, d in enumerate(accounts, 1):
            email = d.get("email", "?")
            print(f"  [{i}/{len(accounts)}] {email}")
            r = _relogin_one(cfg, d, resolved.session_url, dry_run=args.dry_run)
            if r.get("ok"):
                print(f"      新 token: {str(r.get('access_token',''))[:24]}...  cookies={len(r.get('session_cookies') or [])}")
                if not args.dry_run:
                    try:
                        update_account_tokens(
                            cfg, email=email,
                            access_token=r["access_token"],
                            session_token=r.get("session_token", ""),
                            session_cookies=r.get("session_cookies", []),
                            expires=r.get("expires", ""),
                            health_status="ok",
                        )
                        print("      已回写 access_token + session_cookies")
                    except Exception as exc:
                        print(f"      [回写失败] {type(exc).__name__}: {str(exc)[:60]}")
            else:
                print(f"      [x] 重登失败: {r.get('error','')}")
            results.append(r)
            time.sleep(0.5)
    finally:
        resolved.close()

    ok = sum(1 for r in results if r.get("ok"))
    print(f"\n重登成功: {ok}/{len(results)}")
    return 0 if ok == len(results) else 1

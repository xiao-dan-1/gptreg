#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Codex 客户端 OAuth 登录链——用注册机的 password+TOTP 账号换 refresh_token。

背景: 注册机产出的 browser-so 长活账号只有 access_token, 无 refresh_token,
到期 401 就废。本脚本移植 get-rt.js(Codex 客户端 app_EMoamEEZ73f0CkXaXp7hrann +
localhost:1455 redirect) 的 OAuth 链, 用已有密码+TOTP 验证, 拿到 codex refresh_token。

流程(纯 HTTP, 对齐 get-rt.js + login_2fa_pkce.py):
  authorize(PKCE) → authorize/continue(邮箱, pow sentinel) → password/verify(密码, quickjs sentinel)
  → mfa/verify(TOTP) → consent 链(workspace/select → oauth2/auth → consent → code)
  → POST /oauth/token → refresh_token → 用 refresh_token 换 access_token 自证

用法: python capture/research/codex_login_rt.py <email子串> [--proxy URL]
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import pyotp  # noqa: E402
from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402

OAUTH_ISSUER = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # Codex 客户端(与 chatgpt 客户端不同)
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_SCOPE = "openid profile email offline_access"

# chatgpt 客户端(注册机注册时用的 client, JWT 里就是这个): 不强制手机验证, 走 consent 链
CHATGPT_CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
CHATGPT_REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"


def _find_account(sub: str) -> dict:
    for line in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if sub in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {sub}")


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


# ==================== cookie 解码(Flask 签名 cookie payload) ====================
def _decode_flask_cookie(value) -> dict | None:
    val = str(value).strip()
    try:
        val = unquote(val)
    except Exception:
        pass
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    part = val.split(".")[0] if "." in val else val
    pad = "=" * ((4 - len(part) % 4) % 4)
    for enc in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            data = json.loads(enc(part + pad).decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            continue
    return None


def _jar_items(session):
    try:
        return list(session.session.cookies.jar)
    except Exception:
        return []


def _extract_workspace_id(session) -> str:
    for c in _jar_items(session):
        if "oai-client-auth-session" in str(c.name):
            data = _decode_flask_cookie(c.value)
            if not data:
                continue
            ws = data.get("workspaces") or (data.get("client_auth_session") or {}).get("workspaces") or []
            if ws and ws[0] and ws[0].get("id"):
                return str(ws[0]["id"])
    return ""


def _extract_login_verifier(session) -> str:
    for c in _jar_items(session):
        if c.name == "login_session":
            data = _decode_flask_cookie(c.value)
            if data:
                return str(data.get("login_verifier") or data.get("login_challenge") or "")
    return ""


# ==================== 重定向跟随 / code 提取 ====================
def _extract_code(url: str) -> str | None:
    if not url or "code=" not in url:
        return None
    return parse_qs(urlparse(url).query).get("code", [None])[0]


def _abs(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return OAUTH_ISSUER + (url if url.startswith("/") else f"/{url}")


def _pick_continue(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in ("continue_url", "redirect_url", "url", "continueUrl", "redirectUrl"):
        v = obj.get(key)
        if isinstance(v, str) and v:
            return v
    d = obj.get("data")
    if isinstance(d, dict):
        for key in ("continue_url", "redirect_url", "url", "continueUrl", "redirectUrl"):
            v = d.get(key)
            if isinstance(v, str) and v:
                return v
    return ""


def _follow(session, url, headers, max_hops: int = 15, log=lambda m: print(m), redirect_uri: str = OAUTH_REDIRECT_URI):
    """手动跟随重定向链, 提取 ?code=。cookie 由 session 自动管理。"""
    current = url
    last = None
    for i in range(max_hops):
        if current.startswith(redirect_uri) or ("code=" in current and ("localhost" in current or "chatgpt.com" in current)):
            return {"last": last, "code": _extract_code(current), "location": current}
        try:
            r = session.get(current, headers=headers, allow_redirects=False, timeout=30)
        except Exception as e:
            log(f"    follow[{i}] 异常: {str(e)[:60]}")
            return {"last": last, "code": None, "location": current}
        last = r
        loc = _abs(r.headers.get("location", ""))
        if loc and ("code=" in loc or loc.startswith(redirect_uri)):
            return {"last": r, "code": _extract_code(loc), "location": loc}
        if r.status_code in (301, 302, 303, 307, 308) and loc:
            current = loc
            continue
        if r.status_code == 200:
            try:
                obj = r.json()
            except Exception:
                obj = {}
            nxt = _pick_continue(obj)
            if nxt:
                abs_ = _abs(nxt)
                if "code=" in abs_ or abs_.startswith(OAUTH_REDIRECT_URI):
                    return {"last": r, "code": _extract_code(abs_), "location": abs_}
                if abs_ != current:
                    current = abs_
                    continue
        break
    return {"last": last, "code": _extract_code(current), "location": current}


def _org_list(body) -> list:
    if not isinstance(body, dict):
        return []
    d = body.get("data")
    if isinstance(d, dict) and isinstance(d.get("orgs"), list):
        return d["orgs"]
    if isinstance(body.get("orgs"), list):
        return body["orgs"]
    return []


def _submit_consent(session, device_id, consent_url, code_challenge, state, log=lambda m: print(m),
                    client_id: str = OAUTH_CLIENT_ID, redirect_uri: str = OAUTH_REDIRECT_URI):
    """移植 get-rt.js submitConsentAndExtractCode: workspace/select → oauth2/auth → code。"""
    nav = session.auth_navigate_headers(referer=f"{OAUTH_ISSUER}/")
    api = session.auth_api_headers(referer=consent_url)

    # 1) GET consent 页(刷新 cookie, 可能直接给 code)
    step = _follow(session, consent_url, nav, log=log, redirect_uri=redirect_uri)
    if step["code"]:
        log("  consent GET 链直接给出 code")
        return step["code"]

    # 2) workspace_id: cookie 优先, 否则 session_dump
    ws = _extract_workspace_id(session)
    ws_src = "cookie" if ws else ""
    if not ws:
        try:
            d = session.get(f"{OAUTH_ISSUER}/api/accounts/client_auth_session_dump", headers=api, timeout=20)
            dj = d.json() if d.status_code == 200 else {}
            wss = dj.get("workspaces") or (dj.get("client_auth_session") or {}).get("workspaces") or []
            if wss and wss[0] and wss[0].get("id"):
                ws = str(wss[0]["id"])
                ws_src = "session_dump"
        except Exception:
            pass
        if not ws:
            ws = _extract_workspace_id(session)
            ws_src = "cookie-after-dump" if ws else ws_src
    log(f"  workspace_id={ws[:8] + '…' if ws else '(无)'} source={ws_src or '(none)'}")

    auth_code = None
    if ws:
        api2 = session.auth_api_headers(referer=consent_url)
        api2["content-type"] = "application/json"
        rws = session.post(f"{OAUTH_ISSUER}/api/accounts/workspace/select",
                           headers=api2, data=json.dumps({"workspace_id": ws}),
                           allow_redirects=False, timeout=30)
        log(f"  workspace/select -> {rws.status_code}")
        if rws.status_code in (301, 302, 303, 307, 308) and rws.headers.get("location"):
            loc = _abs(rws.headers["location"])
            auth_code = _extract_code(loc)
            if not auth_code:
                auth_code = _follow(session, loc, nav, log=log, redirect_uri=redirect_uri)["code"]
        elif rws.status_code in (200, 201, 204):
            body = {}
            try:
                body = rws.json() if rws.text else {}
            except Exception:
                pass
            ws_next = _pick_continue(body)
            orgs = _org_list(body)
            page_type = (body.get("page") or {}).get("type", "") if isinstance(body, dict) else ""
            auth_code = _extract_code(ws_next)
            need_org = not auth_code and (len(orgs) > 0 or "organization" in (ws_next or "").lower()
                                          or "organization" in page_type.lower())
            if need_org:
                org = next((o for o in orgs if str(o.get("kind", "")).lower() == "personal"), None) or (orgs[0] if orgs else None)
                if org and org.get("id"):
                    ob = {"org_id": str(org["id"])}
                    proj = org.get("projects") or []
                    if proj and proj[0] and proj[0].get("id"):
                        ob["project_id"] = str(proj[0]["id"])
                    rorg = session.post(f"{OAUTH_ISSUER}/api/accounts/organization/select",
                                        headers=api2, data=json.dumps(ob), allow_redirects=False, timeout=30)
                    log(f"  organization/select -> {rorg.status_code}")
                    if rorg.status_code in (301, 302, 303, 307, 308) and rorg.headers.get("location"):
                        loc = _abs(rorg.headers["location"])
                        auth_code = _extract_code(loc)
                        if not auth_code:
                            auth_code = _follow(session, loc, nav, log=log, redirect_uri=redirect_uri)["code"]
                    elif rorg.status_code in (200, 201):
                        org_next = _pick_continue(rorg.json())
                        auth_code = _extract_code(org_next)
                        if not auth_code and org_next:
                            auth_code = _follow(session, _abs(org_next), nav, log=log, redirect_uri=redirect_uri)["code"]
            if not auth_code and ws_next:
                auth_code = _follow(session, _abs(ws_next), nav, log=log, redirect_uri=redirect_uri)["code"]

    # 3) HAR 主路径: oauth2/auth(+login_verifier) → consent_challenge → consent_verifier → code
    if not auth_code and code_challenge and state:
        login_verifier = _extract_login_verifier(session)
        params = {
            "client_id": client_id,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "codex_cli_simplified_flow": "true",
            "id_token_add_organizations": "true",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "state": state,
        }
        if login_verifier:
            params["login_verifier"] = login_verifier
            log(f"  oauth2/auth(login_verifier={login_verifier[:12]}…)")
        else:
            params["prompt"] = "login"
            log("  oauth2/auth(无 login_verifier, prompt=login)")
        oauth2_url = f"{OAUTH_ISSUER}/api/oauth/oauth2/auth?" + urlencode(params)
        auth_code = _follow(session, oauth2_url, nav, max_hops=15, log=log, redirect_uri=redirect_uri)["code"]
        if auth_code:
            log("  oauth2/auth 链提取到 code")
    return auth_code


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sub = args[0] if args else "JasonCopeland"
    proxy_override = None
    if "--proxy" in sys.argv:
        proxy_override = sys.argv[sys.argv.index("--proxy") + 1]
    use_chatgpt = "--chatgpt" in sys.argv
    client_id = CHATGPT_CLIENT_ID if use_chatgpt else OAUTH_CLIENT_ID
    redirect_uri = CHATGPT_REDIRECT_URI if use_chatgpt else OAUTH_REDIRECT_URI
    mode_name = "chatgpt" if use_chatgpt else "codex"

    cfg = load_config()
    r = resolve_proxy(cfg, override=proxy_override)
    acc = _find_account(sub)
    email = acc["email"]
    password = acc.get("password") or ""
    secret = acc.get("totp_secret") or ""
    if not password or not secret:
        print(f"[x] 账号缺少 password/totp_secret: {email}")
        return 1
    print(f"账号: {email}  密码: {password[:4]}***  TOTP: {secret[:6]}...")
    print(f"代理: {r.label()}")

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)

    nav = sess.auth_navigate_headers(referer="https://chatgpt.com/")

    # 1. authorize(客户端 + PKCE)
    ap = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
          "scope": OAUTH_SCOPE, "code_challenge": challenge, "code_challenge_method": "S256", "state": state}
    print(f"\n[1] GET /oauth/authorize ({mode_name} client + PKCE)")
    try:
        # authorize 302 → oauth2/auth(code_challenge…), 必须跟随到 login 页建立 login_session
        followed = _follow(sess, f"{OAUTH_ISSUER}/oauth/authorize?" + urlencode(ap), nav, max_hops=10)
        print(f"  -> 落点: {str(followed['location'] or '')[:90]}")
        has_ls = any(c.name == "login_session" for c in _jar_items(sess))
        print(f"  login_session cookie: {'Y' if has_ls else 'N'}")
        if not has_ls:
            print("  [warn] 未获得 login_session cookie(可能被反爬拦截)")
    except Exception as e:
        print(f"  [x] authorize 异常: {str(e)[:80]}")
        sess.close(); r.close()
        return 1

    # 2. authorize/continue(邮箱)
    def _api(referer):
        h = sess.auth_api_headers(referer=referer)
        h["content-type"] = "application/json"
        return h

    print("[2] POST authorize/continue (邮箱)")
    tok_ac, _ = auth.make_sentinel_headers(sess, None, "authorize_continue", source="pow")
    h2 = _api(f"{OAUTH_ISSUER}/log-in")
    h2["openai-sentinel-token"] = tok_ac
    r2 = sess.post(f"{OAUTH_ISSUER}/api/accounts/authorize/continue",
                   headers=h2, data=json.dumps({"username": {"kind": "email", "value": email}}),
                   allow_redirects=False, timeout=30)
    print(f"  -> {r2.status_code}: {(r2.text or '')[:150]}")
    if r2.status_code != 200:
        print("  [x] authorize/continue 失败")
        sess.close(); r.close()
        return 2
    c2 = r2.json()
    continue_url = c2.get("continue_url", "")
    page_type = (c2.get("page") or {}).get("type", "")
    print(f"  page.type={page_type}")

    # 3. password/verify
    print("[3] POST password/verify")
    tok_pw, _ = get_sentinel_token_via_quickjs(sess, sess.device_id, flow="password_verify", cfg=cfg)
    h3 = _api(f"{OAUTH_ISSUER}/log-in/password")
    h3["openai-sentinel-token"] = tok_pw
    r3 = sess.post(f"{OAUTH_ISSUER}/api/accounts/password/verify",
                   headers=h3, data=json.dumps({"password": password}),
                   allow_redirects=False, timeout=30)
    print(f"  -> {r3.status_code}: {(r3.text or '')[:150]}")
    if r3.status_code != 200:
        print("  [x] 密码验证失败")
        sess.close(); r.close()
        return 3
    c3 = r3.json()
    page_type = (c3.get("page") or {}).get("type", "") or page_type
    continue_url = c3.get("continue_url", "") or continue_url
    try:
        factor_id = (c3.get("page") or {}).get("payload", {}).get("factor_id")
    except Exception:
        factor_id = None
    print(f"  -> 200, page_type={page_type}, factor_id={factor_id}")

    # 4. mfa/verify (TOTP)
    if "mfa" in page_type.lower() or "mfa" in (continue_url or ""):
        print("[4] POST mfa/verify (TOTP)")
        code = pyotp.TOTP(secret).now()
        h4 = _api(f"{OAUTH_ISSUER}/log-in/password")
        r4 = sess.post(f"{OAUTH_ISSUER}/api/accounts/mfa/verify",
                       headers=h4, data=json.dumps({"type": "totp", "id": factor_id, "code": code}),
                       allow_redirects=False, timeout=30)
        print(f"  -> {r4.status_code}: {(r4.text or '')[:150]}")
        if r4.status_code != 200:
            print("  [x] TOTP 验证失败")
            sess.close(); r.close()
            return 4
        c4 = r4.json()
        continue_url = c4.get("continue_url", "") or continue_url
        page_type = (c4.get("page") or {}).get("type", "") or page_type
        print(f"  -> TOTP 通过, page_type={page_type}")

    # 4.5 add_phone 探测: 页面有无 skip/later 选项
    if "add_phone" in page_type.lower() or "add-phone" in (continue_url or ""):
        print("[4.5] 探测 add_phone 页")
        try:
            r_ap = sess.get(_abs(continue_url), headers=nav, allow_redirects=True, timeout=30)
            print(f"  -> {r_ap.status_code} url={str(getattr(r_ap, 'url', ''))[:80]}")
            text = r_ap.text or ""
            for kw in ("skip", "later", "continue without", "not now", "跳过", "以后"):
                if kw.lower() in text.lower():
                    print(f"  [find] '{kw}' 出现在页面")
            import re as _re
            m = _re.findall(r'(?:action|href)="([^"]*(?:skip|later)[^"]*)"', text, _re.I)
            print(f"  [find] skip/later 链接: {m[:5]}")
            print("  --- HTML 前 1200 字符 ---")
            print(text[:1200])
        except Exception as e:
            print(f"  [x] add_phone 探测异常: {str(e)[:80]}")

    # 5. consent 链 → code
    if not continue_url:
        continue_url = f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
    final_url = continue_url if continue_url.startswith("http") else _abs(continue_url)
    print(f"[5] consent 链: {final_url[:90]}")
    auth_code = None
    if re.search(r"oauth2/auth|consent_challenge|login_verifier", final_url):
        f = _follow(sess, final_url, nav, max_hops=15, redirect_uri=redirect_uri)
        auth_code = f["code"]
    if not auth_code:
        consent_url = final_url
        if not re.search(r"consent|sign-in-with-chatgpt|workspace|organization", consent_url):
            consent_url = f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
        auth_code = _submit_consent(sess, sess.device_id, consent_url, challenge, state,
                                    client_id=client_id, redirect_uri=redirect_uri)
    if not auth_code:
        print("  [x] 未能提取 authorization code")
        sess.close(); r.close()
        return 5
    print(f"  ✅ authorization code: {auth_code[:20]}...")

    if use_chatgpt:
        # chatgpt 客户端: 先试 /oauth/token 直接换(Auth0 标准端点), 失败再走 chatgpt callback
        print("[6] POST /oauth/token (chatgpt 客户端 code)")
        rt = sess.post(f"{OAUTH_ISSUER}/oauth/token",
                       headers={"content-type": "application/x-www-form-urlencoded", "user-agent": sess.user_agent},
                       data=urlencode({"grant_type": "authorization_code", "code": auth_code,
                                       "redirect_uri": redirect_uri, "client_id": client_id,
                                       "code_verifier": verifier}),
                       allow_redirects=False, timeout=30)
        print(f"  -> {rt.status_code} loc={(rt.headers.get('location',''))[:120]}")
        tok = {}
        try:
            tok = rt.json()
        except Exception:
            pass
        at = tok.get("access_token") or ""
        refresh_token = tok.get("refresh_token") or ""
        if at:
            print(f"  ✅ /oauth/token 换到新 access_token: {at[:24]}...")
            print(f"  refresh_token: {refresh_token[:24] or '(无)'}")
            acc2 = dict(acc)
            acc2["access_token"] = at
            acc2["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if refresh_token:
                acc2["chatgpt_refresh_token"] = refresh_token
            from gptreg.account_store import save_account
            save_account(cfg, record=acc2)
            print("  💾 已更新账号 access_token")
        else:
            print(f"  [x] /oauth/token 失败: {(rt.text or '')[:150]}")
            print("[6b] 兜底 chatgpt callback + fetch_session")
            cb_url = f"{CHATGPT_REDIRECT_URI}?code={auth_code}&state={state}"
            try:
                r_cb = sess.get(cb_url, headers=nav, allow_redirects=True, timeout=30)
                print(f"  callback -> {r_cb.status_code} url={str(getattr(r_cb, 'url', ''))[:80]}")
            except Exception as e:
                print(f"  callback 异常: {str(e)[:80]}")
            try:
                info = auth.fetch_session(sess)
                at = info.get("accessToken", "")
            except Exception as e:
                print(f"  fetch_session 失败: {str(e)[:100]}")
            if at:
                print(f"  ✅ 新 access_token: {at[:24]}...")
                acc2 = dict(acc)
                acc2["access_token"] = at
                acc2["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                from gptreg.account_store import save_account
                save_account(cfg, record=acc2)
                print("  💾 已更新账号 access_token")
            else:
                print("  [x] 未拿到新 access_token")
        sess.close(); r.close()
        return 0

    # codex 客户端: POST /oauth/token → refresh_token
    print("[6] POST /oauth/token (authorization_code)")
    rt = sess.post(f"{OAUTH_ISSUER}/oauth/token",
                   headers={"content-type": "application/x-www-form-urlencoded", "user-agent": sess.user_agent},
                   data=urlencode({"grant_type": "authorization_code", "code": auth_code,
                                   "redirect_uri": redirect_uri, "client_id": client_id,
                                   "code_verifier": verifier}),
                   allow_redirects=False, timeout=30)
    print(f"  -> {rt.status_code}")
    if rt.status_code != 200:
        print(f"  [x] 换 token 失败: {(rt.text or '')[:200]}")
        sess.close(); r.close()
        return 6
    tok = rt.json()
    refresh_token = tok.get("refresh_token") or ""
    access_token = tok.get("access_token") or ""
    if not refresh_token:
        print("  [x] 响应无 refresh_token")
        sess.close(); r.close()
        return 6
    print(f"  ✅ refresh_token: {refresh_token[:24]}...")
    print(f"  access_token: {access_token[:24]}...")

    # 7. 自证: 用 refresh_token 换 access_token
    print("[7] 自证: refresh_token → access_token")
    rv = sess.post(f"{OAUTH_ISSUER}/oauth/token",
                   headers={"content-type": "application/x-www-form-urlencoded", "user-agent": sess.user_agent,
                            "oai-device-id": sess.device_id},
                   data=urlencode({"grant_type": "refresh_token", "client_id": client_id,
                                   "refresh_token": refresh_token, "scope": OAUTH_SCOPE}),
                   allow_redirects=False, timeout=30)
    print(f"  -> {rv.status_code}")
    if rv.status_code == 200:
        tv = rv.json()
        at2 = tv.get("access_token") or ""
        print(f"  ✅ refresh_token 可用! 新 access_token: {at2[:24]}...")
        # 落盘到账号记录(研究结论保存, 不自动改写生产字段)
        acc2 = dict(acc)
        acc2["codex_refresh_token"] = refresh_token
        acc2["codex_client_id"] = client_id
        acc2["codex_at"] = at2
        acc2["codex_at_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        p = ROOT / "output" / "codex_rt.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(acc2, ensure_ascii=False) + "\n")
        print(f"  💾 已追加到 output/codex_rt.jsonl")
    else:
        print(f"  [x] refresh_token 换 AT 失败: {(rv.text or '')[:200]}")
    sess.close(); r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

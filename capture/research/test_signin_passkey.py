#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对比 ext-passkey-client-capabilities 0000 vs 1111 对 authorize 落点的影响。

假设:codex-register 用 0000 → authorize 302 到 create-account/password(密码注册);
我们用 1111 → 302 到 email-verification(OTP 流程)。参数可能是引导差异的关键。

用法: python capture/test_signin_passkey.py [email]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from urllib.parse import urlencode  # noqa: E402

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg import auth  # noqa: E402


def signin_variant(session, csrf, email, passkey_caps: str) -> str:
    """返回 authorize URL(带指定 passkey capabilities)。"""
    query = {
        "prompt": "login",
        "ext-oai-did": session.device_id,
        "auth_session_logging_id": session.auth_session_logging_id,
        "ext-passkey-client-capabilities": passkey_caps,
        "screen_hint": "login_or_signup",
        "login_hint": email,
    }
    url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(query)
    headers = session.chatgpt_headers()
    headers["content-type"] = "application/x-www-form-urlencoded"
    headers["origin"] = "https://chatgpt.com"
    body = urlencode({"callbackUrl": "https://chatgpt.com/", "csrfToken": csrf, "json": "true"})
    resp = session.post(url, headers=headers, data=body)
    resp.raise_for_status()
    return resp.json().get("url", "")


def run(cfg, proxy_url, email, passkey_caps: str) -> str:
    session = BrowserSession(cfg, proxy=proxy_url)
    try:
        auth.get_providers(session)
        time.sleep(0.2)
        csrf = auth.get_csrf_token(session)
        time.sleep(0.2)
        au = signin_variant(session, csrf, email, passkey_caps)
        time.sleep(0.2)
        final = auth.follow_authorize(session, au, attempts=1)
        return final
    finally:
        try:
            session.session.close()
        except Exception:
            pass


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "JenniferMitchell9500@outlook.com"
    cfg = load_config()
    resolved = resolve_proxy(cfg, override="http://127.0.0.1:10808")
    print(f"邮箱: {email}\n")
    for caps in ("1111", "0000"):
        try:
            final = run(cfg, resolved.session_url, email, caps)
            kind = "create-account/password" if "password" in final else (
                "email-verification" if "email" in final else final[:80])
            print(f"  caps={caps}: 落点 -> {kind}")
        except Exception as exc:
            print(f"  caps={caps}: error {type(exc).__name__}: {str(exc)[:80]}")
    resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

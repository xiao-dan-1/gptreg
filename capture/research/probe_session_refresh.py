#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""研究 OpenAI access_token 刷新机制。

目标:
1. 用存活账号重抓 /api/auth/session, 看完整响应里有哪些刷新字段(refreshToken/expires_at/...)
2. 实测 session_cookies 能否重新换到新 access_token(不依赖 refresh_token)
3. 若 session 响应里有 refreshToken, 验证 refresh 端点能否续期
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config
from gptreg.session import BrowserSession
from gptreg.proxyutil import build_dynamic_proxy, random_sid, resolve_proxy, set_sid


def _load_account(email: str) -> dict:
    p = ROOT / "output" / "accounts.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("email") == email:
            return d
    raise SystemExit(f"找不到账号 {email}")


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "StacyBerry5837+l6fpc8@outlook.com"
    d = _load_account(email)
    print(f"账号: {email}")
    print(f"  落盘 access_token: {(d.get('access_token') or '')[:40]}...")
    print(f"  refresh_token: {str(d.get('refresh_token'))[:30] or '(空)'}")
    cookies = d.get("session_cookies") or []
    print(f"  session_cookies: {len(cookies)} 个")
    print(f"  health_status: {d.get('health_status')}")

    cfg = load_config()
    new_url = set_sid(build_dynamic_proxy(cfg), sid=random_sid(8), sid_len=8)
    rp = resolve_proxy(cfg, override=new_url)
    sess = BrowserSession(cfg, proxy=rp.session_url)

    # 1) 尝试用旧 access_token 重抓 session
    print("\n=== 1. 用旧 access_token 重抓 /api/auth/session ===")
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = f"Bearer {d['access_token']}"
    h["oai-device-id"] = sess.device_id
    h.pop("content-type", None)
    resp = sess.get("https://chatgpt.com/api/auth/session", headers=h, timeout=30)
    print(f"  HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print("  响应 keys:", sorted(data.keys()))
        for k in ("accessToken", "refreshToken", "expires", "expires_at", "user", "oai_device_id"):
            v = data.get(k)
            if k == "user":
                v = {kk: vv for kk, vv in (v or {}).items() if kk in ("email", "name")} if v else v
            print(f"    {k}: {str(v)[:80]}")
    else:
        print(f"  body: {resp.text[:200]}")

    # 2) 用 session_cookies 重抓 session(不带头, 靠 cookie)
    print("\n=== 2. 用 session_cookies 重抓(无 authorization 头) ===")
    sess2 = BrowserSession(cfg, proxy=rp.session_url)
    for c in cookies:
        sess2.session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain"))
    h2 = sess2.chatgpt_headers(referer="https://chatgpt.com/")
    h2.pop("content-type", None)
    resp2 = sess2.get("https://chatgpt.com/api/auth/session", headers=h2, timeout=30)
    print(f"  HTTP {resp2.status_code}")
    if resp2.status_code == 200:
        d2 = resp2.json()
        print("  响应 keys:", sorted(d2.keys()))
        print(f"    accessToken: {str(d2.get('accessToken'))[:40]}...")
        print(f"    refreshToken: {str(d2.get('refreshToken'))[:40] or '(无)'}")
        print(f"    expires: {d2.get('expires')}")
    else:
        print(f"  body: {resp2.text[:200]}")

    rp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

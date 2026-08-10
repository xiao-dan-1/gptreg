#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""直接 POST add_password / change_password 到活账号，验证 eligible:false 是否被端点强制。

用法: python capture/research/probe_addpw_post.py [email 子串] [--set-password xxx]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402


def _find_account(sub: str) -> dict:
    for line in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if sub in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {sub}")


def _inject_cookies(sess: BrowserSession, cookies: list[dict]) -> None:
    for c in cookies or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        domain = c.get("domain") or ""
        for d in (domain, domain.lstrip(".")):
            if not d:
                continue
            try:
                sess.session.cookies.set(c["name"], c["value"] or "", domain=d, path=c.get("path") or "/")
            except Exception:
                pass


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sub = args[0] if args else "ElizabethJames"
    pw = None
    if "--set-password" in sys.argv:
        pw = sys.argv[sys.argv.index("--set-password") + 1]

    cfg = load_config()
    r = resolve_proxy(cfg)
    acc = _find_account(sub)
    print(f"账号: {acc['email']}")
    print(f"代理: {r.label()}")

    sess = BrowserSession(cfg, proxy=r.session_url)
    sess.device_id = acc.get("device_id") or sess.device_id
    _inject_cookies(sess, acc.get("session_cookies") or [])
    at = acc.get("access_token")
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = f"Bearer {at}"
    h["oai-device-id"] = sess.device_id
    h.pop("content-type", None)

    base = "https://chatgpt.com/backend-api/accounts"

    # 1. 完整 security_settings
    try:
        resp = sess.get(base + "/security_settings/info", headers=h, timeout=30)
        print(f"\n=== security_settings/info -> {resp.status_code} ===")
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2)[:800])
    except Exception as e:
        print(f"security_settings 失败: {e}")

    # 2. /me 用户对象（看密码相关字段）
    try:
        resp = sess.get("https://chatgpt.com/backend-api/me", headers=h, timeout=30)
        txt = resp.text
        print(f"\n=== /me -> {resp.status_code}, 长度 {len(txt)} ===")
        # 提取 email_verified / password 相关
        for kw in ("password", "email_verified", "mfa", "has_payg", "phone"):
            import re
            for m in re.finditer(r'"([^"]*' + kw + r'[^"]*)"\s*:\s*([^,}\s]+)', txt[:5000]):
                print(f"  {m.group(1)} = {m.group(2)}")
    except Exception as e:
        print(f"/me 失败: {e}")

    # 3. 直接 POST add_password（即使 eligible:false）
    if pw:
        h2 = dict(h)
        h2["content-type"] = "application/json"
        for path, body in (("/add_password", {"password": pw, "confirm_password": pw}),
                           ("/change_password", {"password": pw, "new_password": pw})):
            try:
                resp = sess.post(base + path, headers=h2, data=json.dumps(body), timeout=30)
                print(f"\n=== POST {path} -> {resp.status_code} ===")
                print((resp.text or "")[:300])
            except Exception as e:
                print(f"POST {path} 失败: {e}")

    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

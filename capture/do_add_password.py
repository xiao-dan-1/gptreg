#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""协议级补设密码 + 2FA:用 cookies 调 chatgpt.com backend-api。

目标:OTP 混合账号(存活好)登录后补设密码,再开 2FA。
发现的端点:
  GET  /backend-api/accounts/add_password/eligibility   补密码资格
  GET  /backend-api/accounts/change_password/eligibility
  GET  /backend-api/accounts/security_settings/info

用法: python capture/do_add_password.py [--email 账号] [--set-password <密码>]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402


def _find_account(email_contains: str = "AliciaFrederick") -> dict:
    lines = [l for l in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for d in [json.loads(l) for l in lines]:
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


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
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="AliciaFrederick")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--set-password", default="", help="设置密码;空则只探测资格")
    args = ap.parse_args()

    cfg = load_config()
    r = resolve_proxy(cfg, override=args.proxy or None)
    acc = _find_account(args.email)
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

    def _get(path: str) -> None:
        try:
            resp = sess.get(base + path, headers=h)
            print(f"  GET {path} -> {resp.status_code}: {(resp.text or '')[:300]}")
        except Exception as exc:
            print(f"  GET {path} 失败: {type(exc).__name__}: {str(exc)[:80]}")

    def _post(path: str, body: dict) -> None:
        h2 = dict(h)
        h2["content-type"] = "application/json"
        try:
            resp = sess.post(base + path, headers=h2, data=json.dumps(body))
            print(f"  POST {path} -> {resp.status_code}: {(resp.text or '')[:300]}")
        except Exception as exc:
            print(f"  POST {path} 失败: {type(exc).__name__}: {str(exc)[:80]}")

    print("\n=== 1. 探测资格 ===")
    _get("/add_password/eligibility")
    _get("/change_password/eligibility")
    _get("/security_settings/info")

    if args.set_password:
        print(f"\n=== 2. 设置密码 ===")
        _post("/add_password", {"password": args.set_password, "confirm_password": args.set_password})

    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

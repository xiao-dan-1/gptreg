#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探索安全页的 2FA 入口:Advanced account security / TOTP / 验证器应用。

目标:确认账号能否开传统 2FA(TOTP),为"全自动开 2FA + 输出 secret"探路。

用法: python capture/explore_2fa.py [--email 账号]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from urllib.parse import urlparse, unquote  # noqa: E402


def _find_account(email_contains: str = "AliciaFrederick") -> dict:
    lines = [l for l in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for d in [json.loads(l) for l in lines]:
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="AliciaFrederick")
    ap.add_argument("--target", default="Advanced account security",
                    help="安全页要探索的目标选项")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    print(f"账号: {acc['email']}  目标: {args.target}")

    from playwright.sync_api import sync_playwright

    for attempt in range(4):
        r = resolve_proxy(cfg, override=args.proxy or None)
        pp = urlparse(r.session_url)
        pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
        if pp.username:
            pw["username"] = unquote(pp.username)
            pw["password"] = unquote(pp.password or "")
        print(f"\n尝试 {attempt + 1}/4  代理: {r.label()}")
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(
                    channel="chrome", headless=True,
                    args=["--disable-blink-features=AutomationControlled",
                          "--remote-debugging-port=9333", "--remote-allow-origins=*"],
                    proxy=pw,
                )
                ctx = b.new_context(
                    user_agent=cfg.get("browser", {}).get("user_agent"),
                    locale="en-US", viewport={"width": 1920, "height": 1080},
                )
                for c in (acc.get("session_cookies") or []):
                    for d in (c.get("domain", "").lstrip("."), c.get("domain", "")):
                        if not d:
                            continue
                        try:
                            ctx.add_cookies([{"name": c["name"], "value": c["value"], "domain": d,
                                              "path": c.get("path") or "/",
                                              "secure": bool(c.get("secure", False))}])
                        except Exception:
                            pass
                pg = ctx.new_page()
                pg.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
                try:
                    pg.wait_for_selector("text=Security keys & passkeys", timeout=25000)
                except Exception:
                    pass
                print(f"安全页 URL: {pg.url}")
                el = pg.locator(f"text={args.target}").first
                if el.count():
                    el.click(force=True)
                    pg.wait_for_timeout(5000)
                    txt = pg.evaluate("() => document.body.innerText")
                    print(f"URL: {pg.url}")
                    print(f"body {len(txt)} 字符:")
                    if txt:
                        print(txt[300:2000].replace(chr(10), " | "))
                    for kw in ("2FA", "two-factor", "authenticator", "TOTP", "code",
                               "phone", "App", "verification", "one-time"):
                        idx = txt.lower().find(kw.lower())
                        if idx >= 0:
                            print(f"  含[{kw}] @{idx}: ...{txt[max(0,idx-25):idx+70]}...")
                else:
                    print(f"找不到 {args.target}")
                    txt = pg.evaluate("() => document.body.innerText")
                    print("body:", txt[300:1000].replace(chr(10), " | "))
                b.close()
            r.close()
            return 0
        except Exception as exc:
            msg = str(exc)
            if "ERR_CONNECTION" in msg or "ERR_INVALID" in msg or "reset" in msg.lower():
                print(f"  代理/连接问题: {msg[:60]}")
                r.close()
                time.sleep(2)
                continue
            print(f"  异常: {msg[:80]}")
            r.close()
            return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

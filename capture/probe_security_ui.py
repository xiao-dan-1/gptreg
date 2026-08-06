#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探测安全页 TOTP 开关 UI: 定位 dispatch 点击是否失效 / re-auth 按钮文本。

用法: python capture/probe_security_ui.py --email 部分邮箱
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from urllib.parse import urlparse, unquote  # noqa: E402


def _find_account(email_contains: str) -> dict:
    lines = [l for l in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for d in [json.loads(l) for l in lines]:
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="elknon")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    print(f"账号: {acc['email']}")

    from playwright.sync_api import sync_playwright

    r = resolve_proxy(cfg, override=args.proxy)
    pp = urlparse(r.session_url)
    pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
    if pp.username:
        pw["username"] = unquote(pp.username)
        pw["password"] = unquote(pp.password or "")

    with sync_playwright() as p:
        b = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
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
        page = ctx.new_page()

        # 1. 打开安全页
        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector("[data-testid=mfa-authenticator-toggle]", timeout=15000)
            print("[OK] mfa-authenticator-toggle 存在")
        except Exception:
            print("[!] mfa-authenticator-toggle 不存在(300ms 后重查)")

        page.wait_for_timeout(2000)
        print(f"安全页 URL: {page.url}")

        # 2. 探测开关元素详情
        try:
            info = page.evaluate("""() => {
                const el = document.querySelector('[data-testid=mfa-authenticator-toggle]');
                if (!el) return {found:false};
                return {
                    found:true,
                    tag:el.tagName,
                    type:el.type||'',
                    role:el.getAttribute('role'),
                    ariaChecked:el.getAttribute('aria-checked'),
                    cls:el.className.slice(0,60),
                    text:(el.innerText||'').trim().slice(0,40),
                    html:(el.outerHTML||'').slice(0,120),
                };
            }""")
            print(f"[开关] {json.dumps(info, ensure_ascii=False)}")
        except Exception as exc:
            print(f"[开关] 探测异常: {exc}")

        # 3. dispatch 点击后页面变化
        try:
            page.evaluate("""() => {
                const el = document.querySelector('[data-testid=mfa-authenticator-toggle]');
                if (el) {
                    for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                        el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                    }
                }
            }""")
            print("[dispatch] 已点击开关")
        except Exception as exc:
            print(f"[dispatch] 异常: {exc}")

        page.wait_for_timeout(3000)
        print(f"点击后 URL: {page.url[:70]}")

        # 4. 点击后页面按钮/输入框
        try:
            btns = page.eval_on_selector_all(
                "button", "els => els.map(e => (e.innerText||'').trim().slice(0,40)).filter(x=>x)")
            print(f"[按钮] {btns[:20]}")
        except Exception as exc:
            print(f"[按钮] 异常: {exc}")
        try:
            inputs = page.eval_on_selector_all(
                "input", "els => els.map(e => ({ty:e.type, n:e.name||'', ph:e.placeholder||''})).slice(0,10)")
            print(f"[输入框] {inputs}")
        except Exception as exc:
            print(f"[输入框] 异常: {exc}")

        b.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

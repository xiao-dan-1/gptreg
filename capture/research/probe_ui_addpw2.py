#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""改进版 UI 探测：正确渲染下 OTP-only 账号 Settings→Account 是否有 Add password。

修复点：SPA 渲染等待、直接 URL 导航、等待网络空闲。

用法: python capture/research/probe_ui_addpw2.py [email 子串] [--proxy URL]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote as _unq

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402


def _find_account(sub: str) -> dict:
    for line in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if sub in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {sub}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sub = args[0] if args else "ElizabethJames"
    proxy_override = None
    if "--proxy" in sys.argv:
        proxy_override = sys.argv[sys.argv.index("--proxy") + 1]

    cfg = load_config()
    r = resolve_proxy(cfg, override=proxy_override)
    acc = _find_account(sub)
    print(f"账号: {acc['email']}")
    print(f"代理: {r.label()}")

    cookies = acc.get("session_cookies") or []
    print(f"cookies: {len(cookies)} 个")

    from playwright.sync_api import sync_playwright

    _pp = urlparse(r.session_url if "://" in r.session_url else "http://" + r.session_url)
    _pw = {"server": f"{_pp.scheme or 'http'}://{_pp.hostname}:{_pp.port}"}
    if _pp.username:
        _pw["username"] = _unq(_pp.username)
        _pw["password"] = _unq(_pp.password or "")

    reqs: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy=_pw,
        )
        ctx = browser.new_context(
            user_agent=cfg.get("browser", {}).get("user_agent"),
            locale="en-US", viewport={"width": 1920, "height": 1080},
        )
        for c in cookies:
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

        def on_req(req):
            if "password" in req.url or "security" in req.url.lower() or "settings" in req.url.lower():
                if "cdn" in req.url or "font" in req.url or ".svg" in req.url:
                    return
                rec = {"method": req.method, "url": req.url[:160]}
                if req.method == "POST":
                    try:
                        rec["body"] = (req.post_data or "")[:200]
                    except Exception:
                        pass
                reqs.append(rec)
                print(f"  [REQ] {req.method} {req.url[:130]}")
                if "body" in rec:
                    print(f"        {rec['body'][:120]}")

        page.on("request", on_req)

        # 1. 直接导航到 Settings (等渲染)
        print("\n[1] 打开 chatgpt.com/settings/account")
        page.goto("https://chatgpt.com/settings/account", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        try:
            text = page.evaluate("() => document.body.innerText")
            print(f"  body {len(text)} 字符")
            for kw in ("Add password", "Password", "Account", "Security and login", "Email", "Sign in", "Passkey", "Change password"):
                idx = text.find(kw)
                if idx >= 0:
                    print(f"  含[{kw}] @{idx}: ...{text[max(0,idx-25):idx+60]}...")
        except Exception as e:
            print(f"  body 读取失败: {e}")

        # 2. 点 Security and login
        print("\n[2] 点击 Security and login")
        clicked = False
        for sel in ["text=Security and login", "text=Security"]:
            try:
                el = page.locator(sel).first
                if el.count():
                    el.click(force=True)
                    page.wait_for_timeout(8000)
                    page.wait_for_timeout(2000)
                    clicked = True
                    print(f"  已点击 {sel}")
                    break
            except Exception as e:
                print(f"  点击 {sel} 失败: {str(e)[:60]}")
        if clicked:
            text = page.evaluate("() => document.body.innerText")
            print(f"  security body {len(text)} 字符")
            for kw in ("Add password", "Password", "Authenticator", "two-factor", "2FA", "MFA",
                       "Passkey", "Email", "Phone", "Sign in", "change password", "Change password"):
                idx = text.lower().find(kw.lower())
                if idx >= 0:
                    print(f"  含[{kw}] @{idx}: ...{text[max(0,idx-25):idx+70]}...")
            # 找按钮/链接
            els = page.eval_on_selector_all(
                "button, a, [role=button]",
                "els => els.map(e => ({t:e.innerText.trim().slice(0,40)}))"
                ".filter(x => x.t)") if False else None
            try:
                btns = page.eval_on_selector_all(
                    "main button, main a, main [role=button], main div[role=button]",
                    "els => els.map(e => (e.innerText||'').trim().slice(0,40)).filter(t => t)")
                print(f"  main 按钮: {btns[:15]}")
            except Exception as e:
                print(f"  按钮枚举失败: {str(e)[:50]}")

        print(f"\n=== 捕获 {len(reqs)} 个安全相关请求 ===")
        for rec in reqs:
            print(f"  {rec['method']} {rec['url']}")
            if "body" in rec:
                print(f"      {rec['body']}")
        out = ROOT / "data" / "ui_addpw2_requests.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(reqs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已存 {out}")
        ctx.close()
        browser.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

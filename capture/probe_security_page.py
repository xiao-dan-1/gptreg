#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""深入 Security 设置页:确认 UI 是否提供 Add password / 2FA 选项。

直接导航 #settings/Security,完整 dump main 区域。找 Add password / 2FA / MFA / Passkey。

用法: python capture/probe_security_page.py [--email 账号]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from urllib.parse import urlparse, unquote as _unq  # noqa: E402


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
    ap.add_argument("--proxy", default="")
    args = ap.parse_args()

    cfg = load_config()
    r = resolve_proxy(cfg, override=args.proxy or None)
    acc = _find_account(args.email)
    print(f"账号: {acc['email']}  代理: {r.label()}")

    cookies = acc.get("session_cookies") or []
    from playwright.sync_api import sync_playwright

    _pp = urlparse(r.session_url if "://" in r.session_url else "http://" + r.session_url)
    _pw = {"server": f"{_pp.scheme or 'http'}://{_pp.hostname}:{_pp.port}"}
    if _pp.username:
        _pw["username"] = _unq(_pp.username)
        _pw["password"] = _unq(_pp.password or "")

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

        # 直接导航到 Security 设置
        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(12000)  # 等 React 渲染

        print(f"URL: {page.url}")
        # 完整 dump main 区域文本
        text = page.evaluate("() => { const m = document.querySelector('main') || document.body; return m.innerText; }")
        print(f"main 文本 {len(text)} 字符:")
        print(text[:2000])
        print("\n=== 关键词 ===")
        for kw in ("Add password", "Password", "2FA", "two-factor", "authenticator", "MFA",
                   "Multi-factor", "Passkey", "Sign in", "Email", "Phone", "Security key"):
            idx = text.lower().find(kw.lower())
            if idx >= 0:
                print(f"  含[{kw}] @{idx}: ...{text[max(0,idx-25):idx+80]}...")

        # dump 所有按钮(找 Add password / 2FA 相关)
        try:
            btns = page.eval_on_selector_all(
                "button",
                "els => els.map(e => ({tx:(e.innerText||'').trim().slice(0,50)})).filter(x => x.tx)",
            )
            print(f"\n=== 按钮({len(btns)} 个) ===")
            for b in btns[:30]:
                print(f"  {b['tx']}")
        except Exception as exc:
            print(f"按钮 dump 失败: {exc}")

        # 点击 Security keys & passkeys 探索通行密钥
        try:
            el = page.locator("text=Security keys & passkeys").first
            if not el.count():
                el = page.locator("text=Security keys").first
            if el.count():
                print("\n=== 点击 Security keys & passkeys ===")
                el.click(force=True)
                page.wait_for_timeout(5000)
                text2 = page.evaluate("() => document.body.innerText")
                print(f"body 文本 {len(text2)} 字符")
                # 打印含 passkey/security key/add 的部分
                low = text2.lower()
                for kw in ("passkey", "security key", "add", "register", "create a"):
                    idx = low.find(kw)
                    if idx >= 0:
                        print(f"  含[{kw}] @{idx}: ...{text2[max(0,idx-30):idx+90]}...")
                # 打印 body 中段(设置内容区,跳过侧边栏)
                print("body[400:1800]:", text2[400:1800].replace(chr(10), ' | '))
                # 点击 Add a Security key or Passkey,走 WebAuthn 注册
                try:
                    add_el = page.locator("text=Add a Security key or Passkey").first
                    if add_el.count():
                        print("\n=== 点击 Add a Security key or Passkey ===")
                        add_el.click(force=True)
                        page.wait_for_timeout(4000)
                        text3 = page.evaluate("() => document.body.innerText")
                        print(f"添加页 body {len(text3)} 字符")
                        for kw in ("passkey", "security key", "WebAuthn", "authenticator",
                                   "register", "Create", "verify", "Verify", "code", "email"):
                            idx = text3.lower().find(kw.lower())
                            if idx >= 0:
                                print(f"  含[{kw}] @{idx}: ...{text3[max(0,idx-25):idx+80]}...")
                    else:
                        print("未找到 Add a Security key or Passkey 按钮")
                except Exception as exc:
                    print(f"添加 passkey 探索失败: {exc}")
        except Exception as exc:
            print(f"passkey 探索失败: {exc}")

        ctx.close()
        browser.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

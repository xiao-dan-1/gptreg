#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针: 安全页 recovery codes(恢复码)的真实入口 + 端点抓包。

用 accounts.jsonl 的 session_cookies(relogin 已更新)注入浏览器 → 安全页 →
  dump MFA/TOTP 区域(recovery 相关元素) → 点 Recovery codes 按钮 → 抓网络端点。

用法: python capture/research/recovery_probe.py [--email 账号关键字] [--proxy URL]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.account_store import load_accounts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="KirstenScott")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = next((d for d in load_accounts(cfg) if args.email in d.get("email", "")), None)
    if not acc:
        print(f"未找到账号 {args.email}")
        return 2
    cookies = acc.get("session_cookies") or []
    print(f"账号: {acc['email']}  cookies={len(cookies)}")

    from playwright.sync_api import sync_playwright

    r = resolve_proxy(cfg, override=args.proxy)
    pp = urlparse(r.session_url)
    pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
    if pp.username:
        pw["username"] = unquote(pp.username)
        pw["password"] = unquote(pp.password or "")

    net: list[dict] = []

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True,
                              args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
                              proxy=pw)
        ctx = b.new_context(user_agent=cfg.get("browser", {}).get("user_agent"),
                            locale="en-US", viewport={"width": 1920, "height": 1080})
        for c in cookies:
            for d in (c.get("domain", "").lstrip("."), c.get("domain", "")):
                if not d:
                    continue
                try:
                    ctx.add_cookies([{"name": c["name"], "value": c["value"], "domain": d,
                                      "path": c.get("path", "/"), "secure": bool(c.get("secure"))}])
                except Exception:
                    pass
        page = ctx.new_page()

        def _on_resp(resp):
            u = resp.url
            if any(k in u for k in ("/mfa", "/recovery", "/security", "/factor", "/totp", "/2fa", "/auth")):
                try:
                    body = resp.text()[:1200]
                except Exception:
                    body = ""
                net.append({"m": resp.request.method, "s": resp.status, "u": u, "b": body})

        page.on("response", _on_resp)

        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        print(f"安全页 URL: {page.url[:80]}")

        # dump MFA 区域 HTML(找 recovery codes)
        print("\n=== 安全页 MFA/Recovery 区域文本 ===")
        txt = page.evaluate("() => document.body.innerText")
        # 找 recovery/backup code 相关段落
        import re
        lines = txt.split("\n")
        for i, ln in enumerate(lines):
            if re.search(r"recovery|backup|restore|code", ln, re.I) and len(ln) < 200:
                print(f"  [{i}] {ln}")
        # dump 前 3000 字符
        print("\n=== body 前 2500 字符 ===")
        print(txt[:2500])

        # 找 recovery 相关可点击元素
        print("\n=== recovery 相关元素 ===")
        els = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('button, a, [role=button], [role=switch], label').forEach(el => {
                const t = (el.innerText || el.value || el.textContent || '').trim().slice(0, 80);
                if (/recovery|backup|restore/i.test(t) && t) out.push({tag: el.tagName, txt: t, html: el.outerHTML.slice(0, 220)});
            });
            return out.slice(0, 15);
        }""")
        for e in els:
            print(f"  <{e['tag']}> {e['txt']!r}")
            print(f"      {e['html']}")

        # 点 Recovery codes(如果有)
        btn = page.locator("button:has-text('Recovery'), button:has-text('Backup'), text=Recovery codes").first
        if btn.count():
            print("\n=== 点击 Recovery codes ===")
            btn.click(force=True)
            page.wait_for_timeout(5000)
            txt2 = page.evaluate("() => document.body.innerText")
            print("点击后 body 片段:", txt2[2000:4000].replace("\n", " | "))
            # dump 网络
        else:
            print("\n=== 无 Recovery codes 按钮 ===")

        print("\n=== 网络请求(mfa/recovery/security/factor) ===")
        for n in net:
            print(f"  {n['m']} {n['s']} {n['u']}")
            if n["b"]:
                print(f"    {n['b'][:400]}")

        b.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

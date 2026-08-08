#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针: 安全页 MFA 区域交互结构 + 实测点 Enroll / Authenticator app 会去哪。

目标: 找对激活 TOTP 的真实入口(之前点 Authenticator app 无反应)。
Phase A 纯协议登录(复用 enable_totp_ui) → 浏览器注入 cookies → 安全页 →
  dump MFA 区域所有可交互元素 outerHTML → 点 Enroll → dump 结果+dialog+网络。

用法: python capture/probe_security_page.py [--email 账号关键字] [--proxy ...]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from capture.enable_totp_ui import _find_account, _phase_a_login  # noqa: E402


def _body(page) -> str:
    try:
        return page.evaluate("() => document.body.innerText")
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="KathrynEverett6196")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    email = acc["email"]
    password = acc.get("password") or ""
    print(f"账号: {email}")

    cookies = _phase_a_login(cfg, acc, email, password, args.proxy)
    if not cookies:
        return 2

    from playwright.sync_api import sync_playwright

    r = resolve_proxy(cfg, override=args.proxy)
    pp = urlparse(r.session_url)
    pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
    if pp.username:
        pw["username"] = unquote(pp.username)
        pw["password"] = unquote(pp.password or "")

    net: list[dict] = []

    with sync_playwright() as p:
        b = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy=pw,
        )
        ctx = b.new_context(user_agent=cfg.get("browser", {}).get("user_agent"),
                            locale="en-US", viewport={"width": 1920, "height": 1080})
        for c in cookies:
            for d in (c["domain"].lstrip("."), c["domain"]):
                if not d:
                    continue
                try:
                    ctx.add_cookies([{"name": c["name"], "value": c["value"], "domain": d,
                                      "path": c["path"], "secure": c["secure"]}])
                except Exception:
                    pass
        page = ctx.new_page()

        def _on_resp(resp):
            if ("/mfa" in resp.url) or ("/security" in resp.url) or ("/advanced" in resp.url) \
                    or ("/2fa" in resp.url) or ("/totp" in resp.url) or ("/factor" in resp.url):
                try:
                    body = resp.text()[:1200]
                except Exception:
                    body = ""
                net.append({"m": resp.request.method, "s": resp.status, "u": resp.url, "b": body})

        page.on("response", _on_resp)

        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        print(f"安全页 URL: {page.url[:70]}")

        # dump MFA 区域完整 HTML(从 "Multi-factor authentication" 到 "Sessions")
        print("\n=== MFA 区域 HTML ===")
        try:
            mfa_html = page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('div, section'));
                const idx = all.findIndex(el => /Multi-factor authentication/i.test(el.innerText || ''));
                if (idx < 0) return 'MFA 区域未找到';
                const el = all[idx];
                // 取 MFA 区域的容器(MFA 到 Sessions 之间)
                let cur = el;
                for (let i = 0; i < 3 && cur; i++) cur = cur.parentElement;
                return cur ? cur.outerHTML.slice(0, 3000) : el.outerHTML.slice(0, 3000);
            }""")
            print(mfa_html[:2800])
        except Exception as exc:
            print(f"  MFA HTML dump 失败: {exc}")

        # dump 所有 mfa 相关 data-testid / toggle 元素
        print("\n=== mfa/toggle 相关元素 ===")
        els = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('[data-testid]').forEach(el => {
                const id = el.getAttribute('data-testid');
                if (/mfa|totp|auth|factor|2fa/i.test(id)) out.push({id: id, tag: el.tagName, html: el.outerHTML.slice(0, 150)});
            });
            return out.slice(0, 20);
        }""")
        for e in els:
            print(f"  [{e['id']}] <{e['tag']}> {e['html']}")

        # dump 关键词交互元素 outerHTML
        print("\n=== 交互元素(MFA/Enroll/Advanced/Authenticator) ===")
        els = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('button, a, [role=button], [role=switch], [role=checkbox], label, input, [class*=toggle], [class*=switch]').forEach(el => {
                const t = (el.innerText || el.value || el.textContent || '').trim().slice(0, 60);
                if (/authenticator|enroll|advanced|mfa|two.factor|2fa|text message|totp/i.test(t) && t) {
                    out.push({tag: el.tagName, role: el.getAttribute('role'), cls: String(el.className||'').slice(0,40), txt: t, html: el.outerHTML.slice(0, 200)});
                }
            });
            return out.slice(0, 25);
        }""")
        for e in els:
            print(f"  <{e['tag']} role={e['role']} cls={e['cls']}> {e['txt']!r}")
            print(f"      {e['html']}")

        # 点 Enroll(Advanced account security)
        print("\n=== 点 Enroll(Advanced account security) ===")
        loc = page.locator("button:has-text('Enroll')").first
        if not loc.count():
            loc = page.locator("text=Enroll").first
        if loc.count():
            loc.click(force=True)
            print("  已点击 Enroll")
        else:
            print("  无 Enroll 按钮")
        page.wait_for_timeout(6000)
        print(f"  URL: {page.url[:80]}")
        try:
            dl = page.evaluate("""() => {
                const d = document.querySelector('[role=dialog], [role=alertdialog], [class*=modal]');
                return d ? d.innerText.slice(0, 900) : null;
            }""")
            if dl:
                print(f"  dialog: {dl[:500]}")
        except Exception:
            pass
        txt = _body(page)
        print(f"  body: {txt[300:1300].replace(chr(10),' | ')}")
        try:
            btns = page.eval_on_selector_all("button", "els => els.map(e => (e.innerText||'').trim().slice(0,40)).filter(x=>x)")
            print(f"  按钮: {btns[:15]}")
        except Exception:
            pass

        print("\n=== 网络请求 ===")
        for n in net:
            print(f"  {n['m']} {n['s']} {n['u']}")
            if n['b']:
                print(f"    {n['b'][:500]}")

        b.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

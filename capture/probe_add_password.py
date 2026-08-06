#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""侦查:混合模式账号登录后补设密码 + 2FA 的流程。

目标(用户真实需求):OTP 混合账号(存活 18h+)登录后设置密码,再开 2FA。
方法:Playwright 导入账号 cookies → 打开 chatgpt.com 设置 → 找"添加密码"入口 → 抓请求。

用法: python capture/probe_add_password.py [--email 账号邮箱]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402


def _find_account(email_contains: str = "AliciaFrederick") -> dict:
    lines = [l for l in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for d in [json.loads(l) for l in lines]:
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


def _dump(page, tag):
    try:
        els = page.eval_on_selector_all(
            "a, button, span, input",
            "els => els.map(e => ({t:e.tagName, tx:(e.innerText||'').trim().slice(0,40), "
            "href:e.href||'', ph:e.placeholder||'', ty:e.type||''}))"
            ".filter(x => x.tx || x.href || x.ph)",
        )
        print(f"  [{tag}] URL={page.url[:80]}")
        for e in els[:40]:
            print(f"      <{e['t']}> {e['tx'][:34]} href={e['href'][:50]} ph={e['ph']} ty={e['ty']}")
    except Exception as exc:
        print(f"  [{tag}] dump 失败: {exc}")


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="AliciaFrederick")
    ap.add_argument("--proxy", default="")
    args = ap.parse_args()

    cfg = load_config()
    r = resolve_proxy(cfg, override=args.proxy or None)
    acc = _find_account(args.email)
    print(f"账号: {acc['email']}")
    print(f"代理: {r.label()}")

    cookies = acc.get("session_cookies") or []
    print(f"cookies: {len(cookies)} 个")

    reqs: list[dict] = []
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlparse, unquote as _unq

    # 拆代理认证(Playwright 不解析 URL 里的 user:pass)
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

        def on_req(req):
            u = req.url
            if "password" in u or "auth.openai.com" in u or "settings" in u.lower():
                if "awe/api" in u or "cdn-cgi" in u or "font" in u or "sdk.js" in u:
                    return
                rec = {"method": req.method, "url": u[:180]}
                if req.method == "POST":
                    try:
                        rec["body"] = (req.post_data or "")[:300]
                    except Exception:
                        pass
                reqs.append(rec)
                print(f"  [REQ] {req.method} {u[:150]}")
                if "body" in rec:
                    print(f"        {rec['body'][:180]}")

        page.on("request", on_req)

        # 1. 打开 chatgpt.com 设置
        page.goto("https://chatgpt.com/settings/account", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(8000)
        _dump(page, "设置-账户页")
        try:
            text = page.evaluate("() => document.body.innerText")
            print(f"  body 文本 {len(text)} 字符")
            for kw in ("password", "Password", "Account", "Email", "Security", "Name"):
                idx = text.find(kw)
                if idx >= 0:
                    print(f"    含[{kw}] @{idx}: ...{text[max(0,idx-30):idx+50]}...")
            # 打印设置区文本(排除侧边栏导航)
            main_idx = text.find("Settings")
            print(f"  body 前 300 字: {text[:300].replace(chr(10), ' | ')}")
        except Exception as exc:
            print(f"  body 读取失败: {exc}")

        # 2. 找设置相关元素(Account/Password/Email/Security)
        try:
            els = page.eval_on_selector_all(
                "button, a, [role=tab], [role=button], h2, h3, input",
                "els => els.map(e => ({tx:(e.innerText||e.value||'').trim().slice(0,50), "
                "href:e.href||'', ph:e.placeholder||'', ty:e.type||''}))"
                ".filter(x => /pass|account|email|secur|password|name|birth/i.test(x.tx+x.ph))",
            )
            print(f"  设置相关元素: {len(els)} 个")
            for e in els[:30]:
                print(f"      tx={e['tx']} href={e['href'][:50]} ph={e['ph']} ty={e['ty']}")
        except Exception as exc:
            print(f"  找设置元素失败: {exc}")
        # 点击 "Security and login" 进入安全设置(force 绕过 onboarding 遮挡)
        clicked = False
        for sel in ["text=Security and login", "text=Security"]:
            try:
                el = page.locator(sel).first
                if el.count():
                    print(f"  点击 {sel} (force)")
                    el.click(force=True)
                    page.wait_for_timeout(4000)
                    _dump(page, "安全设置页")
                    clicked = True
                    break
            except Exception:
                pass
        if clicked:
            try:
                page.wait_for_timeout(3000)
                # 提取 main 区域可见文本(等待渲染)
                text = page.evaluate(
                    "() => { const m = document.querySelector('main') || document.body; "
                    "return m.innerText; }"
                )
                print(f"  [安全页 main] {len(text)} 字符")
                for kw in ("Add password", "Password", "2FA", "two-factor", "authenticator",
                           "MFA", "Multi-factor", "change password", "Change password",
                           "Passkey", "sign-in", "Email", "Phone", "Multi-factor auth"):
                    idx = text.lower().find(kw.lower())
                    if idx >= 0:
                        print(f"    含[{kw}] @{idx}: ...{text[max(0,idx-20):idx+70]}...")
                # 打印 main 区完整内容(前 1200 字符)
                print(f"  [main 内容] {text[:1200].replace(chr(10), ' | ')}")
            except Exception as exc:
                print(f"  安全页 main 读取失败: {exc}")

        # 3. dump 当前密码相关表单
        _dump(page, "最终")

        print(f"\n=== 捕获 {len(reqs)} 个请求 ===")
        for rec in reqs:
            print(f"  {rec['method']} {rec['url']}")
            if "body" in rec:
                print(f"      {rec['body']}")
        out = ROOT / "data" / "add_password_requests.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(reqs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已存 {out}")
        ctx.close()
        browser.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

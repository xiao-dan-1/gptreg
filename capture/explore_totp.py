#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码账号开启 TOTP 2FA:点 Authenticator app → 抓取 secret/otpauth URI。

用法: python capture/explore_totp.py [--email 密码账号]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from urllib.parse import urlparse, unquote  # noqa: E402


def _find_account(email_contains: str = "LeslieChavez") -> dict:
    lines = [l for l in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for d in [json.loads(l) for l in lines]:
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="LeslieChavez6274+i0m23u")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    print(f"账号: {acc['email']}")

    from playwright.sync_api import sync_playwright

    for attempt in range(3):
        r = resolve_proxy(cfg, override=args.proxy)
        pp = urlparse(r.session_url)
        pw = {"server": f"{pp.scheme}://{pp.hostname}:{pp.port}"}
        if pp.username:
            pw["username"] = unquote(pp.username)
            pw["password"] = unquote(pp.password or "")
        try:
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
                page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_selector("text=Authenticator app", timeout=25000)
                except Exception:
                    pass
                print(f"安全页 URL: {page.url}")

                # 用 JS 找到并点击 "Authenticator app"(MFA 区域的选项)
                # dump MFA 区域 DOM 结构(找 Authenticator app 的可点击元素)
                try:
                    mfa = page.eval_on_selector_all(
                        "*", "els => els.filter(e => {"
                        "const t=(e.innerText||'').trim();"
                        "return t.includes('Authenticator app') && t.length<120;"
                        "}).map(e => ({tag:e.tagName, role:e.getAttribute('role')||'', "
                        "tx:(e.innerText||'').trim().slice(0,60), cl:(e.className||'').slice(0,60)}))"
                    )
                    print(f"MFA 元素: {mfa[:8]}")
                except Exception as exc:
                    print(f"MFA dump 失败: {exc}")
                # 点击 mfa-authenticator-toggle 开关(开启 TOTP)
                toggle = page.locator("[data-testid=mfa-authenticator-toggle]").first
                if toggle.count():
                    print("找到 mfa-authenticator-toggle 开关,点击")
                    toggle.click(force=True)
                else:
                    print("找不到 mfa-authenticator-toggle 开关")
                    try:
                        tg2 = page.locator("button:has-text('Authenticator app')").first
                        if tg2.count():
                            tg2.click(force=True)
                            print("点击 Authenticator app 按钮")
                    except Exception:
                        pass
                page.wait_for_timeout(8000)

                # 监听 otpauth/totp/secret 相关网络请求(抓取 TOTP secret)
                def _on_res(resp):
                    u = resp.url
                    if any(k in u for k in ("otpauth", "totp", "2fa", "mfa", "authenticator", "secret")):
                        try:
                            body = resp.text()[:500]
                            print(f"  [TOTP-RESP] {resp.status} {u[-80:]} -> {body[:200]}")
                        except Exception:
                            pass

                page.on("response", _on_res)

                txt = page.evaluate("() => document.body.innerText")
                print(f"\nbody {len(txt)} 字符:")
                # 找 TOTP 关键:secret / otpauth / QR / 6-digit / setup key
                low = txt.lower()
                for kw in ("otpauth", "secret", "setup key", "qrcode", "qr code",
                           "scan", "6-digit", "verification code", "add authenticator",
                           "enter the code", "totp"):
                    idx = low.find(kw)
                    if idx >= 0:
                        print(f"  含[{kw}] @{idx}: ...{txt[max(0,idx-30):idx+90]}...")
                # dump 二维码 img(src)
                try:
                    imgs = page.eval_on_selector_all(
                        "img", "els => els.map(e => ({src:(e.src||'').slice(0,80), alt:e.alt||''}))"
                    )
                    print(f"\n图片: {imgs[:8]}")
                except Exception:
                    pass
                # dump 输入框/按钮
                try:
                    els = page.eval_on_selector_all(
                        "input, button",
                        "els => els.map(e => ({ty:e.type, ph:e.placeholder||'', tx:(e.innerText||'').trim().slice(0,40)}))",
                    )
                    print(f"\n输入/按钮: {[e for e in els if e['ph'] or e['tx']][:15]}")
                except Exception:
                    pass
                # 点 "Trouble scanning?" 显示 secret key(文本方式)
                try:
                    ts = page.locator("text=Trouble scanning").first
                    if ts.count():
                        ts.click()
                        page.wait_for_timeout(2000)
                        txt2 = page.evaluate("() => document.body.innerText")
                        print(f"\n[Trouble scanning] body {len(txt2)} 字符:")
                        low = txt2.lower()
                        for kw in ("secret", "key", "manual", "otpauth", "base32", "setup"):
                            idx = low.find(kw)
                            if idx >= 0:
                                print(f"  含[{kw}] @{idx}: ...{txt2[max(0,idx-30):idx+90]}...")
                except Exception as exc:
                    print(f"Trouble scanning 失败: {exc}")
                b.close()
            r.close()
            return 0
        except Exception as exc:
            print(f"尝试 {attempt+1} 失败: {str(exc)[:70]}")
            r.close()
            if "ERR_CONNECTION" in str(exc) or "reset" in str(exc).lower():
                import time
                time.sleep(2)
                continue
            return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

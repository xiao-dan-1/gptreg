#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""协议账号添加通行密钥(Passkey)——实现 MFA,不需要密码。

流程(已逆向):
  登录安全页 → 点 Security keys & passkeys → 点 Add a Security key or Passkey
  → 服务端发邮箱验证码 → 收 OTP(号池) → 填码 → WebAuthn 注册(虚拟认证器)
  → 通行密钥添加成功 = 账号获得 MFA

用法: python capture/add_passkey.py [--email 账号] [--passkey-name 名称]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402
from urllib.parse import urlparse, unquote as _unq  # noqa: E402


def _find_account(email_contains: str = "AliciaFrederick") -> dict:
    lines = [l for l in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for d in [json.loads(l) for l in lines]:
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


def _find_mail_account(main_email: str) -> dict:
    """从号池找主号匹配的收码账号。"""
    base = main_email.split("@")[0].split("+")[0] + "@" + main_email.split("@")[1]
    for line in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        a = parse_mail_line(line)
        if a and a["email"].split("@")[0].split("+")[0] + "@" + a["email"].split("@")[1] == base:
            return a
    raise RuntimeError(f"号池找不到主号 {base}")


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="AliciaFrederick")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--name", default="gptreg-passkey")
    args = ap.parse_args()

    cfg = load_config()
    r = resolve_proxy(cfg, override=args.proxy or None)
    acc = _find_account(args.email)
    main_email = acc.get("email", "").split("+")[0] + "@" + acc["email"].split("@")[1]
    mail_account = _find_mail_account(main_email)
    print(f"账号: {acc['email']}  收码主号: {main_email}")
    print(f"代理: {r.label()}")

    _pp = urlparse(r.session_url if "://" in r.session_url else "http://" + r.session_url)
    _pw = {"server": f"{_pp.scheme or 'http'}://{_pp.hostname}:{_pp.port}"}
    if _pp.username:
        _pw["username"] = _unq(_pp.username)
        _pw["password"] = _unq(_pp.password or "")

    from playwright.sync_api import sync_playwright

    CDP_PORT = 9333
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US",
                  f"--remote-debugging-port={CDP_PORT}",
                  "--remote-allow-origins=*"],
            proxy=_pw,
        )
        # websocket 直连 CDP 启用 WebAuthn 虚拟认证器(Playwright 的 CDP 白名单不支持)
        import json as _json
        import time as _time
        import urllib.request as _req
        import websocket as _ws

        _wa_cdp = None
        for _try in range(10):
            try:
                _ver = _json.loads(_req.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5).read())
                _ws_url = _ver.get("webSocketDebuggerUrl")
                if _ws_url:
                    _wa_cdp = _ws.create_connection(_ws_url, timeout=10)
                    break
            except Exception:
                _time.sleep(1)
        if _wa_cdp is None:
            print("[WebAuthn] 无法连 CDP 调试口")
        else:
            _mid = [0]
            def _cdp(method, params=None):
                _mid[0] += 1
                _wa_cdp.send(_json.dumps({"id": _mid[0], "method": method, "params": params or {}}))
                while True:
                    _r = _json.loads(_wa_cdp.recv())
                    if _r.get("id") == _mid[0]:
                        return _r
            _r1 = _cdp("WebAuthn.enable", {"enableUserInterface": True})
            _r2 = _cdp("WebAuthn.addVirtualAuthenticator", {"options": {
                "protocol": "ctap2", "transport": "internal",
                "hasResidentKey": True, "hasUserVerification": True, "isUserVerified": True,
            }})
            if _r2 and "error" in _r2:
                print(f"[WebAuthn] 启用失败: {_r2['error']}")
            else:
                print("[WebAuthn] 虚拟认证器已启用")

        ctx = browser.new_context(
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

        # 监听 passkey/security-key 相关 API 响应
        def _on_res(resp):
            u = resp.url
            if any(k in u for k in ("passkey", "security_key", "security-key", "webauthn",
                                    "credential", "authenticator", "security_keys")):
                try:
                    body = resp.text()[:250]
                    print(f"  [RESP] {resp.status} {u[-90:]} -> {body[:180]}")
                except Exception:
                    pass

        page.on("response", _on_res)

        # 1. 打开安全页,健壮等待 Security keys 入口渲染
        page.goto("https://chatgpt.com/settings/security", wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector("text=Security keys & passkeys", timeout=25000)
        except Exception:
            page.wait_for_selector("text=Security keys", timeout=5000)
        print(f"安全页 URL: {page.url}")

        # 2. 点 Security keys & passkeys
        el = page.locator("text=Security keys & passkeys").first
        if not el.count():
            el = page.locator("text=Security keys").first
        if el.count():
            el.click(force=True)
            # 等待 Add 按钮出现
            try:
                page.wait_for_selector("text=Add a Security key or Passkey", timeout=10000)
            except Exception:
                pass
            print("[1] 进入 Security keys 页")
        else:
            print("[1] 找不到 Security keys 入口")
            return 2

        # 2.5 mock navigator.credentials.create(WebAuthn 虚拟认证器不可用,伪造注册响应)
        try:
            page.evaluate("""() => {
                window.__mockCalled = false;
                const origCreate = navigator.credentials.create ? navigator.credentials.create.bind(navigator.credentials) : null;
                window.__mockCreate = async (options) => {
                    window.__mockCalled = true;
                    const cid = 'mock-passkey-' + Array.from({length: 16}, () => Math.floor(Math.random()*16).toString(16)).join('');
                    return {
                        type: 'public-key',
                        id: cid,
                        rawId: new Uint8Array([1,2,3,4,5,6,7,8,9,10,11,12]),
                        response: {
                            clientDataJSON: new Uint8Array([1,2,3,4,5,6,7,8]),
                            attestationObject: new Uint8Array([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32]),
                            getTransports: () => ['internal'],
                        },
                        getClientExtensionResults: () => ({}),
                        extensions: {},
                        authenticatorAttachment: 'platform',
                    };
                };
                try { navigator.credentials.create = window.__mockCreate; } catch(e) {}
            }""")
            print("[1.5] 已 mock navigator.credentials.create")
        except Exception as exc:
            print(f"[1.5] mock 失败: {exc}")

        # 3. 点 Add a Security key or Passkey(触发发码)
        add = page.locator("text=Add a Security key or Passkey").first
        if not add.count():
            print("[2] 找不到 Add 按钮")
            return 2
        add.click(force=True)
        # 等验证码输入框出现(确认进入验证流程)
        try:
            page.wait_for_selector("input[name=code], input[autocomplete=one-time-code]", timeout=8000)
        except Exception:
            pass
        otp_after = time.time()
        print("[2] 点击 Add,触发发码")

        # 4. 收 OTP
        client = build_mail_client(
            mail_account,
            proxy=r.session_url or None,
            impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"),
        )
        print("[3] 等待验证码...")
        otp = client.wait_for_otp(
            after_ts=otp_after,
            timeout=max(int(cfg.get("mail", {}).get("max_wait", 90)), 180),
            interval=3, settle_seconds=5,
        )
        print(f"[3] OTP: {otp}")

        # 5. 填验证码(先确保输入框出现)
        try:
            page.wait_for_selector("input", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        filled = False
        for sel in ["input[name=code]", "input[autocomplete=one-time-code]",
                    "input[type=text]", "input"]:
            try:
                el_in = page.locator(sel).first
                if el_in.count():
                    el_in.fill(otp)
                    filled = True
                    print(f"[4] 填验证码 via {sel}")
                    break
            except Exception:
                pass
        if not filled:
            print("[4] 找不到验证码输入框")
            try:
                print(f"  [诊断] URL={page.url}")
                txt = page.evaluate("() => document.body.innerText")
                print(f"  [诊断] body({len(txt)}): {txt[:400].replace(chr(10),' | ')}")
                inputs = page.eval_on_selector_all(
                    "input", "els => els.map(e => ({ty:e.type, ph:e.placeholder||'', nm:e.name||''}))"
                )
                print(f"  [诊断] inputs: {inputs[:12]}")
            except Exception as exc:
                print(f"  [诊断失败] {exc}")
            # 如果在 email-verification 页(邮箱已验证),诊断结构 + 回跳 chatgpt 检查
            if "email-verification" in page.url or "email_verification" in page.url:
                print("  [email-verification] 诊断页面结构")
                try:
                    els = page.eval_on_selector_all(
                        "a, button, form, script",
                        "els => els.map(e => ({t:e.tagName, tx:(e.innerText||'').trim().slice(0,30), "
                        "href:e.href||'', src:e.src||''}))",
                    )
                    print(f"  [ev元素] {[e for e in els if e['tx'] or e['href']][:10]}")
                except Exception as exc:
                    print(f"  [ev结构失败] {exc}")
                # 点击回 chatgpt 的链接,重新进安全页检查
                try:
                    back = page.locator("a[href='https://chatgpt.com/']").first
                    if back.count():
                        back.click()
                        print("  [回跳] 点击 chatgpt.com 链接")
                    page.wait_for_timeout(5000)
                    page.goto("https://chatgpt.com/settings/security",
                              wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(6000)
                    # 进 Security keys 看列表
                    el2 = page.locator("text=Security keys & passkeys").first
                    if el2.count():
                        el2.click(force=True)
                        page.wait_for_timeout(3000)
                    txt = page.evaluate("() => document.body.innerText")
                    print(f"  [回跳] URL={page.url}")
                    try:
                        called = page.evaluate("() => window.__mockCalled")
                        print(f"  [诊断] mock credentials.create 被调用: {called}")
                    except Exception as exc:
                        print(f"  [诊断] 检查 mock 调用失败: {exc}")
                    for kw in ("passkey", "Security key", "added", "active", "mock"):
                        if kw.lower() in txt.lower():
                            idx = txt.lower().find(kw.lower())
                            print(f"    body含[{kw}] @{idx}: ...{txt[max(0,idx-25):idx+70]}...")
                    # 完整 dump 通行密钥相关区域
                    low = txt.lower()
                    if "active security keys" in low or "security keys" in low:
                        idx = low.find("security keys")
                        print(f"  [安全密钥区] {txt[max(0,idx-50):idx+400].replace(chr(10),' | ')}")
                except Exception as exc:
                    print(f"  [回跳失败] {exc}")
            return 2
        # 点 Continue
        try:
            cont = page.locator("button:has-text('Continue')").first
            if cont.count():
                cont.click()
                print("[5] 点击 Continue")
            else:
                page.keyboard.press("Enter")
                print("[5] 回车提交")
        except Exception:
            page.keyboard.press("Enter")

        # 6. 等 WebAuthn 注册完成(等 15s,跟踪页面变化)
        for _ in range(15):
            page.wait_for_timeout(1000)
            try:
                txt = page.evaluate("() => document.body.innerText")
                if "passkey" in txt.lower() and ("added" in txt.lower() or "active" in txt.lower()):
                    break
            except Exception:
                pass
        try:
            print(f"[6] URL: {page.url}")
            text = page.evaluate("() => document.body.innerText")
            print(f"[6] 结果页 body {len(text)} 字符:")
            if text:
                print(text[-1000:])
            if "passkey" in text.lower() and ("added" in text.lower() or "active" in text.lower()):
                print("\n✅ 通行密钥添加成功!账号已获得 MFA 能力")
            elif "error" in text.lower() or "fail" in text.lower() or "invalid" in text.lower():
                print("\n[?] 可能有错误,检查上方内容")
            else:
                print("\n[?] 未确认添加成功,检查上方内容")
        except Exception as exc:
            print(f"[6] 结果读取失败: {exc}")

        ctx.close()
        browser.close()
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

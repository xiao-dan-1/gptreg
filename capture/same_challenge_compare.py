#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""同一 challenge 下对比 vm `_n` 与浏览器 `_n` 的 t。

决定性实验：生存差距到底是 turnstile widget 执行，还是 `_n` 的环境保真。
- vm 侧：requirements → /req 拿 challenge C → vm solve(C) → vm_t
- 浏览器侧：Playwright 拦截 /req 返回同一个 C → 浏览器 token() → browser_t
- 解码对比（同 C → 前缀可对齐）

用法: python capture/same_challenge_compare.py
"""
from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.sentinel_quickjs import _run_action, _ensure_sdk, _fingerprint_payload, _quickjs_script  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402

FLOW = "oauth_create_account"
SENTINEL_REQ_URL = "https://chatgpt.com/backend-api/sentinel/req"


def b64d(s: str) -> bytes:
    s2 = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s2 + "=" * (-len(s2) % 4))


def main() -> int:
    cfg = load_config("config.yaml")
    proxy = str((cfg.get("proxy") or {}).get("http") or "http://127.0.0.1:7890")
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    device_id = str(uuid.uuid4())

    session = BrowserSession(cfg, proxy=proxy)
    script = _quickjs_script()
    sdk_file = _ensure_sdk(session, sv, 60000)
    fp = _fingerprint_payload(cfg, device_id, sv)

    # 1) 浏览器先跑完整流：捕获它的 challenge C_browser + browser_t
    from playwright.sync_api import sync_playwright
    browser_t = None
    browser_challenge = None
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": proxy},
        )
        ctx = browser.new_context(
            user_agent=session.user_agent,
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        page.context.add_cookies([{"name": "oai-did", "value": device_id, "domain": ".openai.com", "path": "/"}])

        def pass_through(route):
            nonlocal browser_challenge
            resp = route.fetch()
            try:
                browser_challenge = resp.json()
            except Exception:
                pass
            route.fulfill(response=resp)
        page.route("**/sentinel/req", pass_through)

        page.goto("https://auth.openai.com/about-you", wait_until="domcontentloaded", timeout=45000)
        page.mouse.move(120, 160); page.mouse.move(420, 280, steps=8); page.mouse.wheel(0, 200)
        page.wait_for_timeout(400)
        page.add_script_tag(url="https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js")
        page.wait_for_timeout(800)
        ok = page.evaluate("""() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')""")
        print("浏览器 SDK 暴露:", ok)
        if ok:
            r = page.evaluate(
                """async (flow) => {
                    try {
                      const t = await window.SentinelSDK.token(flow);
                      return typeof t === 'string' ? t : JSON.stringify(t);
                    } catch (e) { return 'ERR: ' + String(e); }
                }""", FLOW)
            if isinstance(r, str) and r.startswith("ERR"):
                print("浏览器 token 失败:", r[:200])
            else:
                try:
                    tj = json.loads(r) if isinstance(r, str) else r
                    browser_t = str(tj.get("t") or "")
                    print("浏览器 token t len:", len(browser_t))
                except Exception as e:
                    print("解析失败:", e, str(r)[:200])
        page.wait_for_timeout(3000)
        ctx.close()

    if not browser_challenge:
        print("未捕获浏览器 challenge")
        return 1
    challenge = browser_challenge
    c_token = str(challenge.get("token") or "")
    print("浏览器 challenge token len:", len(c_token), "turnstile.required:", challenge.get("turnstile", {}).get("required"))
    (ROOT / "data" / "same_challenge.json").write_text(json.dumps(challenge, ensure_ascii=False), encoding="utf-8")

    if not browser_t:
        print("未拿到浏览器 t")
        return 1

    # 2) vm solve(C_browser) → vm_t（同一 challenge）
    vm_req = _run_action(script, sdk_file, "requirements", fp, 120000)
    vm_request_p = str(vm_req.get("request_p") or "")
    print("vm request_p len:", len(vm_request_p))
    solve_payload = dict(fp)
    solve_payload.update({"request_p": vm_request_p, "challenge": challenge, "flow": FLOW})
    t0 = time.time()
    solved = _run_action(script, sdk_file, "solve", solve_payload, 120000)
    vm_t = str(solved.get("t") or "")
    print(f"vm solve(C_browser) t len: {len(vm_t)} ({time.time() - t0:.0f}s)")

    if not browser_t:
        print("未拿到浏览器 t")
        return 1

    # 5) 对比（同 C → 对齐共享前缀）
    b = b64d(browser_t)
    v = b64d(vm_t)
    print(f"\n浏览器 t: {len(b)} bytes; vm t: {len(v)} bytes; 差 {len(b) - len(v)}")
    n = min(len(b), len(v))
    # 找最长共享前缀
    pref = 0
    while pref < n and b[pref] == v[pref]:
        pref += 1
    print(f"共享前缀长度: {pref}")
    # 前缀之后逐字节匹配率
    div_b, div_v = b[pref:], v[pref:]
    m = min(len(div_b), len(div_v))
    matches = sum(1 for i in range(m) if div_b[i] == div_v[i])
    print(f"分歧区: 浏览器 {len(div_b)}B vs vm {len(div_v)}B; 重叠 {m}B; 字节匹配 {matches}/{m} = {100 * matches / max(m, 1):.1f}%")
    print(f"总匹配率: {100 * sum(1 for i in range(n) if b[i] == v[i]) / n:.1f}%")

    (ROOT / "data" / "same_challenge_result.json").write_text(json.dumps({
        "device_id": device_id, "vm_t_len": len(vm_t), "browser_t_len": len(browser_t),
        "shared_prefix": pref, "divergence_browser": len(div_b), "divergence_vm": len(div_v),
        "divergence_match_pct": round(100 * matches / max(m, 1), 1),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

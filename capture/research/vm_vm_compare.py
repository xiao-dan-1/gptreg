#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""随机性基线:同 challenge 下 vm 两次独立 solve 的 t 差异 vs vm-browser 差异。

若 vm-vm 匹配率也低(~20%),说明 t 里含大量每次运行的随机量,
「vm t vs 浏览器 t 字节匹配率」根本不是环境保真度的有效指标——
之前"结构性墙"结论需重估。若 vm-vm 匹配率高(80%+),则 17% 主要是环境差异。

用法: python capture/vm_vm_compare.py
"""
from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.sentinel_quickjs import _run_action, _ensure_sdk, _fingerprint_payload, _quickjs_script  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402

FLOW = "oauth_create_account"


def b64d(s: str) -> bytes:
    s2 = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s2 + "=" * (-len(s2) % 4))


def compare(browser_t: str, vm_t: str) -> dict:
    b = b64d(browser_t)
    v = b64d(vm_t)
    n = min(len(b), len(v))
    pref = 0
    while pref < n and b[pref] == v[pref]:
        pref += 1
    div_b, div_v = b[pref:], v[pref:]
    m = min(len(div_b), len(div_v))
    matches = sum(1 for i in range(m) if div_b[i] == div_v[i])
    total = sum(1 for i in range(n) if b[i] == v[i])
    return {
        "a_bytes": len(b), "b_bytes": len(v), "diff_bytes": len(b) - len(v),
        "shared_prefix": pref, "divergence_match_pct": round(100 * matches / max(m, 1), 1),
        "total_match_pct": round(100 * total / max(n, 1), 1),
    }


def main() -> int:
    cfg = load_config("config.yaml")
    proxy = str((cfg.get("proxy") or {}).get("http") or "http://127.0.0.1:7890")
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    device_id = str(uuid.uuid4())

    session = BrowserSession(cfg, proxy=proxy)
    script = _quickjs_script()
    sdk_file = _ensure_sdk(session, sv, 60000)
    fp = _fingerprint_payload(cfg, device_id, sv)

    from playwright.sync_api import sync_playwright
    browser_t = None
    browser_challenge = None
    browser_request_p = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": proxy},
        )
        ctx = browser.new_context(
            user_agent=session.user_agent, locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        page.context.add_cookies([{"name": "oai-did", "value": device_id, "domain": ".openai.com", "path": "/"}])

        def pass_through(route):
            nonlocal browser_challenge, browser_request_p
            resp = route.fetch()
            try:
                browser_challenge = resp.json()
                body = route.request.post_data or ""
                try:
                    bj = json.loads(body)
                    bj_p = str(bj.get("p") or "")
                    if bj_p:
                        browser_request_p = bj_p
                except Exception:
                    pass
            except Exception:
                pass
            route.fulfill(response=resp)
        page.route("**/sentinel/req", pass_through)

        page.goto("https://auth.openai.com/about-you", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        page.add_script_tag(url=f"https://sentinel.openai.com/sentinel/{sv}/sdk.js")
        page.wait_for_timeout(800)
        ok = page.evaluate("""() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')""")
        print("浏览器 SDK 暴露:", ok)
        if ok:
            r = page.evaluate(
                """async (flow) => { try {
                    const t = await window.SentinelSDK.token(flow);
                    return typeof t === 'string' ? t : JSON.stringify(t);
                } catch (e) { return 'ERR: ' + String(e); } }""", FLOW)
            if isinstance(r, str) and r.startswith("ERR"):
                print("浏览器 token 失败:", r[:200])
            else:
                try:
                    tj = json.loads(r) if isinstance(r, str) else r
                    browser_t = str(tj.get("t") or "")
                    print("浏览器 token t len:", len(browser_t))
                except Exception as e:
                    print("解析失败:", e, str(r)[:200])
        ctx.close()
        browser.close()

    if not browser_challenge or not browser_t or not browser_request_p:
        print("浏览器侧数据不完整")
        return 1
    challenge = browser_challenge
    print(f"challenge len={len(str(challenge.get('token') or ''))} request_p len={len(browser_request_p)}")

    base = dict(fp)
    base.update({"request_p": browser_request_p, "challenge": challenge, "flow": FLOW, "skip_so": True})

    ts = []
    for i in range(2):
        t0 = time.time()
        solved = _run_action(script, sdk_file, "solve", dict(base), 120000)
        vm_t = str(solved.get("t") or "")
        el = time.time() - t0
        if len(vm_t) < 50 or vm_t.startswith("MDogU3ludGF4"):
            print(f"  vm{i+1}: 假 t len={len(vm_t)}")
            continue
        ts.append(vm_t)
        print(f"  vm{i+1}: t len={len(vm_t)} ({el:.0f}s)")

    if len(ts) >= 2:
        c_vv = compare(ts[0], ts[1])
        print(f"\n=== vm-vm(同输入双跑) ===")
        print(f"  len {c_vv['a_bytes']} vs {c_vv['b_bytes']} 差 {c_vv['diff_bytes']} 前缀 {c_vv['shared_prefix']} 匹配 {c_vv['total_match_pct']}%")
    if ts:
        c_vb = compare(browser_t, ts[-1])
        print(f"=== vm-browser ===")
        print(f"  len {c_vb['a_bytes']} vs {c_vb['b_bytes']} 差 {c_vb['diff_bytes']} 前缀 {c_vb['shared_prefix']} 匹配 {c_vb['total_match_pct']}%")

    (ROOT / "data" / "vm_vm_compare_result.json").write_text(json.dumps({
        "device_id": device_id, "browser_request_p": browser_request_p,
        "browser_t_b64": browser_t,
        "vm_ts_b64": ts,
        "vm_vm": c_vv if len(ts) >= 2 else None,
        "vm_browser": c_vb if ts else None,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n结果已存 data/vm_vm_compare_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路径完整性穷举2：注入完整 window 快照（含所有对象）看 rand 调用是否暴涨。
浏览器 541 次 Math.random vs vm 9 次——补环境若触发 vm 完整字节码路径，rand 应暴涨。
用法: python capture/t_path2_exp.py
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
    win_snap = {}
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
            try:
                resp = route.fetch()
            except Exception:
                return
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
                route.fulfill(response=resp)
            except Exception:
                pass
        page.route("**/sentinel/req", pass_through)

        page.goto("https://auth.openai.com/about-you", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        page.add_script_tag(url=f"https://sentinel.openai.com/sentinel/{sv}/sdk.js")
        page.wait_for_timeout(800)
        ok = page.evaluate("""() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')""")
        print("浏览器 SDK 暴露:", ok, flush=True)
        if ok:
            r = page.evaluate(
                """async (flow) => { try {
                    const t = await window.SentinelSDK.token(flow);
                    return typeof t === 'string' ? t : JSON.stringify(t);
                } catch (e) { return 'ERR: ' + String(e); } }""", FLOW)
            if isinstance(r, str) and r.startswith("ERR"):
                print("浏览器 token 失败:", r[:200], flush=True)
            else:
                try:
                    tj = json.loads(r) if isinstance(r, str) else r
                    browser_t = str(tj.get("t") or "")
                    print("浏览器 token t len:", len(browser_t), flush=True)
                except Exception as e:
                    print("解析失败:", e, str(r)[:200], flush=True)
        # 完整 window 快照（含对象原型探测）
        win_snap = page.evaluate("""() => {
            const std = new Set(["window","self","top","parent","document","navigator","screen",
                "localStorage","sessionStorage","performance","history","location","chrome","CSS",
                "indexedDB","fetch","setTimeout","setInterval","clearTimeout","clearInterval",
                "atob","btoa","crypto","Event","CustomEvent","MessageChannel","matchMedia",
                "getComputedStyle","postMessage","addEventListener","removeEventListener","dispatchEvent",
                "TextEncoder","TextDecoder","URL","URLSearchParams","console","JSON","Math","Object",
                "Reflect","Promise","Array","String","Number","Boolean","Date","RegExp","Error","Map",
                "Set","WeakMap","WeakSet","Function","Symbol","BigInt","undefined","NaN","Infinity",
                "globalThis","Intl","Proxy","frames","opener","length","name","origin","external",
                "isSecureContext","devicePixelRatio","innerWidth","innerHeight","outerWidth","outerHeight",
                "closed","status","defaultStatus","styleMedia","visualViewport","caches","customElements",
                "crossOriginIsolated","scheduler","speechSynthesis","trustedTypes"]);
            const out = {};
            for (const k of Object.getOwnPropertyNames(globalThis)) {
                if (std.has(k)) continue;
                try {
                    const v = globalThis[k];
                    if (v == null) { out[k] = v; continue; }
                    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') { out[k] = v; continue; }
                    if (typeof v === 'object') {
                        const s = JSON.stringify(v);
                        if (s && s.length <= 3000) out[k] = s;
                    }
                } catch (e) { }
            }
            return out;
        }""")
        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()

    if not browser_challenge or not browser_t or not browser_request_p:
        print("浏览器侧数据不完整", flush=True)
        return 1
    challenge = browser_challenge
    print(f"challenge len={len(str(challenge.get('token') or ''))} win_snap={len(win_snap)}键", flush=True)

    groups = {
        "G0_base": {},
        "G1_win": {"win_extra": win_snap},
    }
    print("\n=== vm solve（同 challenge, 完整路径, 记录 rand） ===", flush=True)
    results = {}
    for name, extra in groups.items():
        payload = dict(fp)
        payload.update({"request_p": browser_request_p, "challenge": challenge, "flow": FLOW, "skip_so": True})
        payload.update(extra)
        t0 = time.time()
        try:
            solved = _run_action(script, sdk_file, "solve", payload, 60000)
            vm_t = str(solved.get("t") or "")
            el = time.time() - t0
            tn = len(solved.get("trace_now") or [])
            tr = len(solved.get("trace_rand") or [])
            if len(vm_t) < 50 or vm_t.startswith("MDogU3ludGF4"):
                results[name] = {"error": f"假 t({len(vm_t)})", "rand": tr, "now": tn}
                print(f"  {name}: 假 t len={len(vm_t)} rand={tr} now={tn}", flush=True)
                continue
            b = b64d(browser_t)
            v = b64d(vm_t)
            pref = 0
            while pref < min(len(b), len(v)) and b[pref] == v[pref]:
                pref += 1
            results[name] = {"t_len": len(vm_t), "diff": len(b) - len(v), "prefix": pref,
                             "rand": tr, "now": tn, "elapsed": round(el, 1)}
            print(f"  {name}: t={len(vm_t)}c 差={len(b)-len(v)}B 前缀={pref} rand={tr} now={tn} ({el:.1f}s)", flush=True)
        except Exception as exc:
            results[name] = {"error": f"{type(exc).__name__}: {str(exc)[:80]}"}
            print(f"  {name}: 异常 {results[name]['error']}", flush=True)

    (ROOT / "data" / "t_path2_exp_result.json").write_text(json.dumps({
        "device_id": device_id, "browser_t_b64": browser_t,
        "win_snap_keys": list(win_snap.keys()), "groups": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n结果已存 data/t_path2_exp_result.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

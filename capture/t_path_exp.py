#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路径完整性实验：vm 的 _n 只调用 9 次 Math.random vs browser 510 次。
定位让 vm 字节码执行路径不完整的环境值——逐步补环境，看 rand/now 调用次数是否暴涨。

用法: python capture/t_path_exp.py
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
    seed = json.loads((ROOT / "data" / "seed_quickjs.json").read_text(encoding="utf-8"))

    from playwright.sync_api import sync_playwright
    browser_t = None
    browser_challenge = None
    browser_request_p = ""
    b_trace = {}
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
        page.evaluate("""() => {
            window.__trace = { now: [], rand: [] };
            const pn = performance.now.bind(performance);
            performance.now = () => { const v = pn(); window.__trace.now.push(v); return v; };
            const mr = Math.random;
            Math.random = () => { const v = mr(); window.__trace.rand.push(v); return v; };
            window.__trStart = () => { window.__trace.now.length = 0; window.__trace.rand.length = 0; };
        }""")
        page.add_script_tag(url=f"https://sentinel.openai.com/sentinel/{sv}/sdk.js")
        page.wait_for_timeout(800)
        ok = page.evaluate("""() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')""")
        print("浏览器 SDK 暴露:", ok, flush=True)
        page.evaluate("() => window.__trStart()")
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
        b_trace = page.evaluate("() => ({ now: window.__trace.now, rand: window.__trace.rand })")
        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()

    if not browser_challenge or not browser_t or not browser_request_p:
        print("浏览器侧数据不完整", flush=True)
        return 1
    challenge = browser_challenge
    print(f"browser t={len(b_trace.get('now') or [])} now? trace now={len(b_trace.get('now') or [])} rand={len(b_trace.get('rand') or [])}", flush=True)

    rctx = seed.get("react_router_full") or {}
    ls = seed.get("ls_extra") or {}
    font = seed.get("font_gbcr") or {}
    groups = {
        "G0_base": {},
        "G1_rctx": {"react_router_full": rctx},
        "G2_rctx_win": {"react_router_full": rctx, "win_extra": None},
        "G3_all": {"react_router_full": rctx, "ls_extra": ls, "font_gbcr": font},
    }
    print("\n=== vm solve（同 challenge, 完整路径） ===", flush=True)
    results = {}
    for name, extra in groups.items():
        payload = dict(fp)
        payload.update({"request_p": browser_request_p, "challenge": challenge, "flow": FLOW, "skip_so": True})
        payload.update({k: v for k, v in extra.items() if v is not None})
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

    (ROOT / "data" / "t_path_exp_result.json").write_text(json.dumps({
        "device_id": device_id, "browser_t_b64": browser_t,
        "browser_rand": len(b_trace.get("rand") or []), "browser_now": len(b_trace.get("now") or []),
        "groups": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n结果已存 data/t_path_exp_result.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

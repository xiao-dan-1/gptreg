#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""t 缺口差分实验（wrapper 加速后）：测时序跳变 + 快照补全对 t 长度/匹配率的影响。

候选：blob1 慢路径（ABSCOND |now-prev|>2000ms 触发，vm 快永不触发 → t 更短）。

用法: python capture/t_diff_exp.py
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
        "browser_bytes": len(b), "vm_bytes": len(v), "diff_bytes": len(b) - len(v),
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
    rctx_full = None
    ls_full = {}
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
        page.wait_for_timeout(2000)
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
        rctx_full = page.evaluate("""() => {
            try { return JSON.parse(JSON.stringify(globalThis.__reactRouterContext)); } catch (e) { return null; }
        }""")
        ls_full = page.evaluate("""() => { const m={}; for(let i=0;i<localStorage.length;i++)
            { const k=localStorage.key(i); m[k]=String(localStorage.getItem(k)); } return m; }""")
        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()

    if not browser_challenge or not browser_t or not browser_request_p:
        print("浏览器侧数据不完整", flush=True)
        return 1
    challenge = browser_challenge
    print(f"challenge len={len(str(challenge.get('token') or ''))} request_p len={len(browser_request_p)}", flush=True)
    print(f"rctx_full size={None if rctx_full is None else len(json.dumps(rctx_full, ensure_ascii=False))} ls={len(ls_full)}键", flush=True)

    groups = {
        "G0_base": {},
        "G1_jump": {"perf_jump": True},
        "G2_snap": {"react_router_full": rctx_full, "ls_extra": ls_full},
        "G3_jump_snap": {"perf_jump": True, "react_router_full": rctx_full, "ls_extra": ls_full},
    }
    print("\n=== vm solve（同 challenge, skip_fp+skip_so, 快速） ===", flush=True)
    results = {}
    for name, extra in groups.items():
        payload = dict(fp)
        payload.update({"request_p": browser_request_p, "challenge": challenge, "flow": FLOW,
                        "skip_so": True, "skip_fp": True})
        payload.update(extra)
        t0 = time.time()
        try:
            solved = _run_action(script, sdk_file, "solve", payload, 60000)
            vm_t = str(solved.get("t") or "")
            el = time.time() - t0
            if len(vm_t) < 50 or vm_t.startswith("MDogU3ludGF4"):
                results[name] = {"error": f"假 t({len(vm_t)})", "elapsed": round(el, 1),
                                 "ms_fp": solved.get("ms_fp"), "ms_n": solved.get("ms_n")}
                print(f"  {name}: 假 t len={len(vm_t)} ms_n={solved.get('ms_n')} ({el:.1f}s)", flush=True)
                continue
            cmp = compare(browser_t, vm_t)
            cmp["elapsed"] = round(el, 1)
            cmp["ms_n"] = solved.get("ms_n")
            cmp["vm_t_b64"] = vm_t
            results[name] = cmp
            print(f"  {name}: vm={cmp['vm_bytes']}B 差={cmp['diff_bytes']}B 匹配={cmp['total_match_pct']}% "
                  f"ms_n={solved.get('ms_n')} ({el:.1f}s)", flush=True)
        except Exception as exc:
            results[name] = {"error": f"{type(exc).__name__}: {str(exc)[:80]}"}
            print(f"  {name}: 异常 {results[name]['error']}", flush=True)

    (ROOT / "data" / "t_diff_exp_result.json").write_text(json.dumps({
        "device_id": device_id, "browser_t_b64": browser_t,
        "rctx_full_size": None if rctx_full is None else len(json.dumps(rctx_full, ensure_ascii=False)),
        "ls_keys": list(ls_full.keys()), "groups": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n结果已存 data/t_diff_exp_result.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

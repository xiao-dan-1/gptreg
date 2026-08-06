#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""t 缺口补全对照实验（同 challenge）：
浏览器产真 t；对同一 challenge 跑多组 vm solve，逐步补全已知缺口，
量化每组 t 长度与字节匹配率变化。

缺口候选（来自 blob2 反汇编）:
  - localStorage keys（浏览器 statsig 4 键 vs vm 2 键）
  - 字体渲染测量 getBoundingClientRect（浏览器 y=482 vs vm 0）
  - window.__reactRouterContext（真浏览器存在，路径到 undefined）

用法: python capture/t_gap_exp.py
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
    ls_before = {}
    ls_after = {}
    font_true = None
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
        page.wait_for_timeout(2000)
        ls_before = page.evaluate("""() => { const m={}; for(let i=0;i<localStorage.length;i++)
            { const k=localStorage.key(i); m[k]=String(localStorage.getItem(k)); } return m; }""")
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
        ls_after = page.evaluate("""() => { const m={}; for(let i=0;i<localStorage.length;i++)
            { const k=localStorage.key(i); m[k]=String(localStorage.getItem(k)); } return m; }""")

        # 字体测量真值（blob2 同款: fontFamily/fontSize/innerText + getBoundingClientRect）
        blob2 = json.loads((ROOT / "data" / "blob2_program.json").read_text(encoding="utf-8"))
        fname = fsize = ftext = None
        for inst in blob2:
            if isinstance(inst, list) and len(inst) >= 3 and inst[1] == "fontFamily" and isinstance(inst[2], str):
                fname = inst[2]
            if isinstance(inst, list) and len(inst) >= 3 and inst[1] == "fontSize" and isinstance(inst[2], str):
                fsize = inst[2]
            if isinstance(inst, list) and len(inst) >= 3 and inst[1] == "innerText" and isinstance(inst[2], str):
                ftext = inst[2]
        fname = fname or "Lucida Sans Unicode"
        fsize = fsize or "14px"
        font_true = page.evaluate(
            """(p) => { const d=document.createElement('div');
                d.style.position='fixed'; d.style.visibility='hidden';
                d.style.fontFamily=p.fontFamily; d.style.fontSize=p.fontSize; d.innerText=p.innerText;
                document.body.appendChild(d); const r=d.getBoundingClientRect(); document.body.removeChild(d);
                return {x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,left:r.left,right:r.right,bottom:r.bottom}; }""",
            {"fontFamily": fname, "fontSize": fsize, "innerText": ftext})
        print("字体测量真值:", font_true)
        ctx.close()
        browser.close()

    if not browser_challenge or not browser_t or not browser_request_p:
        print("浏览器侧数据不完整")
        return 1
    challenge = browser_challenge
    print(f"浏览器 challenge len={len(str(challenge.get('token') or ''))} request_p len={len(browser_request_p)}")
    print(f"localStorage 前 {len(ls_before)} 键 / 后 {len(ls_after)} 键; SDK 新增: {set(ls_after) - set(ls_before)}")
    (ROOT / "data" / "same_challenge.json").write_text(json.dumps(challenge, ensure_ascii=False), encoding="utf-8")

    groups = {
        "G0_base": {},
        "G1_ls": {"ls_extra": ls_before},
        "G2_font": {"font_gbcr": font_true},
        "G3_react": {"react_router": True},
        "G4_all": {"ls_extra": ls_before, "font_gbcr": font_true, "react_router": True},
    }
    print("\n=== 多组 vm solve（同 challenge, skip_so） ===")
    results = {}
    for name, extra in groups.items():
        payload = dict(fp)
        payload.update({"request_p": browser_request_p, "challenge": challenge, "flow": FLOW, "skip_so": True})
        payload.update(extra)
        t0 = time.time()
        try:
            solved = _run_action(script, sdk_file, "solve", payload, 120000)
            vm_t = str(solved.get("t") or "")
            el = time.time() - t0
            if len(vm_t) < 50 or vm_t.startswith("MDogU3ludGF4"):
                results[name] = {"error": f"假 t({len(vm_t)})", "elapsed": round(el, 1)}
                print(f"  {name}: 假 t len={len(vm_t)} ({el:.0f}s)")
                continue
            cmp = compare(browser_t, vm_t)
            cmp["elapsed"] = round(el, 1)
            results[name] = cmp
            print(f"  {name}: vm={cmp['vm_bytes']}B 差={cmp['diff_bytes']}B "
                  f"前缀={cmp['shared_prefix']} 匹配={cmp['total_match_pct']}% ({el:.0f}s)")
        except Exception as exc:
            results[name] = {"error": f"{type(exc).__name__}: {str(exc)[:80]}"}
            print(f"  {name}: 异常 {results[name]['error']}")

    (ROOT / "data" / "t_gap_exp_result.json").write_text(json.dumps({
        "device_id": device_id, "browser_t_b64": browser_t,
        "ls_before_keys": list(ls_before.keys()), "ls_after_new": sorted(set(ls_after) - set(ls_before)),
        "font_true": font_true, "groups": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n结果已存 data/t_gap_exp_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

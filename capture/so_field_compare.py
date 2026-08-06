#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""so 行为字段死因确认：browser vs vm 的 __oai_so_* 字段对比。

死因假设：quickjs 的 so 行为段(36 字段)全 null，browser 有真实行为累积值。
服务端深度校验检测 so 行为段空 = 机器注册。

用法: python capture/so_field_compare.py
"""
from __future__ import annotations

import base64
import json
import os
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
    browser_so = None
    browser_so_fields = {}
    browser_challenge = None
    browser_request_p = ""
    browser_t = None
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
        # 充分模拟人类行为（collector 采集，参考 simulateBehavior 轨迹）
        for x, y in [(280,180),(310,205),(340,230),(375,255),(400,280),(435,310),(465,340),(500,365),(525,390)]:
            page.mouse.move(x, y)
            page.wait_for_timeout(80)
        page.mouse.wheel(0, 260)
        page.wait_for_timeout(120)
        page.mouse.move(505, 400)
        page.wait_for_timeout(100)
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
        page.mouse.click(505, 400)
        page.wait_for_timeout(1500)  # 等 collector 处理
        ok = page.evaluate("""() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')""")
        print("浏览器 SDK 暴露:", ok, flush=True)
        if ok:
            r = page.evaluate(
                """async (flow) => { try {
                    const t = await window.SentinelSDK.token(flow);
                    const so = typeof window.SentinelSDK.sessionObserverToken === 'function'
                        ? await window.SentinelSDK.sessionObserverToken(flow) : null;
                    return JSON.stringify({ t, so });
                } catch (e) { return 'ERR: ' + String(e); } }""", FLOW)
            if isinstance(r, str) and r.startswith("ERR"):
                print("浏览器 token 失败:", r[:200], flush=True)
            else:
                try:
                    tj = json.loads(r)
                    browser_t = str(tj.get("t") or "")
                    browser_so = tj.get("so")
                    if isinstance(browser_so, dict):
                        browser_so = json.dumps(browser_so, ensure_ascii=False)
                    print("浏览器 t len:", len(browser_t), "so len:", len(browser_so or ""), flush=True)
                except Exception as e:
                    print("解析失败:", e, str(r)[:200], flush=True)
        # 采集 browser 的 __oai_so_* 字段
        browser_so_fields = page.evaluate("""() => {
            const out = {};
            for (const k of Object.getOwnPropertyNames(globalThis)) {
                if (k.startsWith('__oai_so_')) {
                    const v = globalThis[k];
                    out[k] = typeof v === 'function' ? '[func]' : String(v);
                }
            }
            return out;
        }""")
        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()

    if not browser_challenge or not browser_t:
        print("浏览器侧数据不完整", flush=True)
        return 1
    challenge = browser_challenge
    print(f"\n=== browser __oai_so_* 字段 ===", flush=True)
    for k in sorted(browser_so_fields):
        print(f"  {k} = {browser_so_fields[k][:40]}", flush=True)

    # vm solve（同 challenge, 完整路径, 产 so）
    print(f"\n=== vm solve（同 challenge, 完整路径 + so） ===", flush=True)
    payload = dict(fp)
    payload.update({"request_p": browser_request_p, "challenge": challenge, "flow": FLOW,
                    "so_wait_collector_ms": int(os.environ.get("SO_WAIT_MS", "0") or 0),
                    "inject_oai_so": os.environ.get("INJECT_OAI", "0") == "1",
                    "strip_node_globals": os.environ.get("STRIP_NODE", "0") == "1",
                    "debug_reflect": os.environ.get("DEBUG_REFLECT", "0") == "1",
                    "simulate_behavior": os.environ.get("SIMULATE", "0") == "1"})
    t0 = time.time()
    solved = _run_action(script, sdk_file, "solve", payload, 60000)
    vm_t = str(solved.get("t") or "")
    vm_so = solved.get("so")
    vm_oai = solved.get("oai_so") or {}
    print(f"vm t len={len(vm_t)} so len={len(str(vm_so or ''))} elapsed={time.time()-t0:.1f}s", flush=True)
    print(f"patch_n={solved.get('patch_n')} patch_jt={solved.get('patch_jt')}", flush=True)
    print(f"so_jt_err: {str(solved.get('so_jt_err'))[:500] if solved.get('so_jt_err') else 'null'}", flush=True)
    print(f"t_err: {str(solved.get('t_err'))[:500] if solved.get('t_err') else 'null'}", flush=True)
    print(f"setref_err: {str(solved.get('setref_err'))[:500] if solved.get('setref_err') else 'null'}", flush=True)
    el = solved.get("event_log") or []
    print(f"event_log({len(el)}): {json.dumps(el[:30], ensure_ascii=False)[:700]}", flush=True)
    print(f"t_last: {str(solved.get('t_last'))[:200] if solved.get('t_last') else 'null'}", flush=True)
    ad = solved.get("at_dump") or []
    print(f"at_dump({len(ad)}): {json.dumps(ad[:40], ensure_ascii=False)[:600]}", flush=True)
    for k, v in ad:
        if k == "8":
            print(f"  opcode 8 = {v}", flush=True)
    print(f"so_rej: {str(solved.get('so_rej'))[:600] if solved.get('so_rej') else 'null'}", flush=True)
    print(f"so_raw head: {str(vm_so)[:100]}", flush=True)
    print(f"\n=== vm __oai_so_* 字段 ===", flush=True)
    for k in sorted(vm_oai):
        print(f"  {k} = {vm_oai[k][:40]}", flush=True)

    # 对比
    print(f"\n=== 字段对比 ===", flush=True)
    all_keys = sorted(set(browser_so_fields) | set(vm_oai))
    diff = []
    for k in all_keys:
        b = browser_so_fields.get(k)
        v = vm_oai.get(k)
        b_norm = str(b or '').replace('[func]', 'func')
        v_norm = str(v or '').replace('[func]', 'func')
        if b_norm != v_norm:
            diff.append((k, b, v))
    print(f"差异字段 {len(diff)}/{len(all_keys)}:", flush=True)
    for k, b, v in diff[:20]:
        print(f"  {k}: browser={str(b)[:30]} | vm={str(v)[:30]}", flush=True)

    (ROOT / "data" / "so_field_compare_result.json").write_text(json.dumps({
        "device_id": device_id, "browser_t_b64": browser_t,
        "browser_so": browser_so, "vm_so": str(vm_so or ""),
        "browser_so_fields": browser_so_fields, "vm_oai": vm_oai,
        "browser_so_len": len(browser_so or ""), "vm_so_len": len(str(vm_so or "")),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n结果已存 data/so_field_compare_result.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

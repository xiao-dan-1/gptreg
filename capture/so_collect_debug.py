#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断 vm collector：se 后初始化状态 vs 事件后状态。

定位 vm 里字段初始化失败(全 null) vs 事件更新失败(只 lx/ly)。
用法: python capture/so_collect_debug.py
"""
from __future__ import annotations

import json
import os
import sys
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
    challenge = None
    request_p = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": proxy},
        )
        ctx = browser.new_context(user_agent=session.user_agent, locale="en-US",
                                  viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.context.add_cookies([{"name": "oai-did", "value": device_id, "domain": ".openai.com", "path": "/"}])
        def pass_through(route):
            nonlocal challenge, request_p
            try:
                resp = route.fetch()
                challenge = resp.json()
                body = route.request.post_data or ""
                try:
                    bj = json.loads(body)
                    request_p = str(bj.get("p") or "")
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
        page.evaluate("""async (flow) => { try { await window.SentinelSDK.token(flow); } catch (e) {} }""", FLOW)
        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()

    if not challenge or not request_p:
        print("未拿到 challenge/request_p")
        return 1

    # collect_test: skip_events=true 看初始化, false 看事件后
    for skip in [True, False]:
        payload = dict(fp)
        payload.update({
            "action": "collect_test", "request_p": request_p, "challenge": challenge,
            "flow": FLOW, "skip_events": skip, "se_wait_ms": int(os.environ.get("SE_WAIT", "800")),
        })
        r = _run_action(script, sdk_file, "collect_test", payload, 60000)
        oai = r.get("oai_before") if skip else r.get("oai_after")
        print(f"\n=== collect_test skip_events={skip} ===", flush=True)
        if r.get("error"):
            print("ERROR:", r["error"], flush=True)
            continue
        if skip:
            print(f"oai_before({len(oai or {})} 字段):", flush=True)
        else:
            print(f"oai_after({len(oai or {})} 字段):", flush=True)
        for k in sorted(oai or {}):
            print(f"  {k} = {str(oai[k])[:45]}", flush=True)
        if skip:
            print(f"event_log: {json.dumps(r.get('event_log'), ensure_ascii=False)[:300]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

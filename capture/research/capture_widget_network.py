#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""捕获 turnstile widget 的完整网络流（challenge-platform）。

复用 harvest 的页面流（auth.openai.com + 注入 sdk + 调 token()），额外拦截
challenges.cloudflare.com 的所有请求：main.js、动态加载的子脚本、oneshot POST
（body + 响应）、以及任何 .wasm。

用法: python capture/capture_widget_network.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
import sys
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

FLOW = "oauth_create_account"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "turnstile_capture"))
    ap.add_argument("--proxy", default="http://127.0.0.1:7890")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    scripts = {}  # url -> body

    def on_req(req):
        if "challenges.cloudflare.com" not in req.url:
            return
        e = {
            "ts": time.time(), "phase": "request", "method": req.method, "url": req.url,
            "type": req.resource_type, "post_data": None,
        }
        try:
            e["post_data"] = req.post_data
        except Exception:
            pass
        entries.append(e)
        print(f"[req ] {req.method} {req.url[:150]}")

    def on_resp(resp):
        if "challenges.cloudflare.com" not in resp.url:
            return
        e = {
            "ts": time.time(), "phase": "response", "url": resp.url,
            "status": resp.status, "headers": {k: v for k, v in resp.headers.items() if k.lower() not in ("set-cookie",)},
        }
        entries.append(e)
        try:
            body = resp.body()
            is_wasm = body[:4] == b"\0asm"
            if resp.url.endswith(".js") or "oneshot" in resp.url:
                scripts[resp.url] = body.decode("utf-8", errors="replace")
            print(f"[resp] {resp.status} {resp.url[:120]} len={len(body)} wasm={is_wasm}")
            if is_wasm:
                (out / ("wasm_" + str(abs(hash(resp.url))) + ".wasm")).write_bytes(body)
        except Exception:
            pass

    with sync_playwright() as p:
        # 与 harvest 一致：系统 Chrome + 防自动化 flag + UA/locale/viewport（避免严格 CSP）
        browser = p.chromium.launch(
            channel="chrome",
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": args.proxy},
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        device_id = str(uuid.uuid4())
        page.context.add_cookies([{
            "name": "oai-did", "value": device_id, "domain": ".openai.com", "path": "/",
        }])
        page.on("request", on_req)
        page.on("response", on_resp)
        page.goto("https://auth.openai.com/about-you", wait_until="domcontentloaded", timeout=45000)
        page.mouse.move(120, 160); page.mouse.move(420, 280, steps=8); page.mouse.wheel(0, 200)
        page.wait_for_timeout(400)
        page.add_script_tag(url="https://sentinel.openai.com/backend-api/sentinel/sdk.js")
        page.wait_for_timeout(800)
        ok = page.evaluate("""() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')""")
        print("SDK 暴露:", ok)
        if ok:
            # 每次 token() 触发一次 /req → 新 challenge；turnstile.required 不定，
            # 循环到 widget 真正加载（捕获到 challenge-platform 事件）。
            for attempt in range(5):
                n0 = len(entries)
                r = page.evaluate(
                    """async (flow) => {
                        try {
                          const t = await window.SentinelSDK.token(flow);
                          const tj = typeof t === 'string' ? JSON.parse(t) : t;
                          return { len: (typeof t === 'string' ? t.length : JSON.stringify(t).length), t_field: String((tj && tj.t) || '').length };
                        } catch (e) { return 'ERR: ' + String(e); }
                    }""", FLOW)
                page.wait_for_timeout(4000)
                new_events = len(entries) - n0
                print(f"attempt {attempt + 1}: token={r} 新增事件={new_events}")
                if new_events > 0:
                    break
        # 等 widget 收尾
        page.wait_for_timeout(6000)
        ctx.close()

    (out / "entries.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "scripts.json").write_text(json.dumps(scripts, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n保存 {len(entries)} 条 events → {out}")
    print(f"捕获 {len(scripts)} 个脚本/oneshot 响应")
    return 0


if __name__ == "__main__":
    sys.exit(main())

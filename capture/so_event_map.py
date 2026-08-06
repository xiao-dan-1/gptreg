#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""科学逆向第一步：建立「事件类型 → __oai_so_* 字段更新」映射表。

真浏览器里 token() 启动 collector 后，逐类触发单个事件，对比字段快照，
定位每个事件更新哪些字段。这是逆向 collector_dx 监听器逻辑的地面真值。

用法: python capture/so_event_map.py
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402

from playwright.sync_api import sync_playwright


def snap(page) -> dict:
    return page.evaluate("""() => {
        const out = {};
        for (const k of Object.getOwnPropertyNames(globalThis)) {
            if (k.startsWith('__oai_so_')) {
                const v = globalThis[k];
                out[k] = (typeof v === 'function') ? '[func]' : String(v);
            }
        }
        return out;
    }""")


def diff(prev: dict, cur: dict) -> list[str]:
    changed = []
    for k in sorted(set(prev) | set(cur)):
        if prev.get(k) != cur.get(k):
            changed.append(f"{k}: {prev.get(k)} → {cur.get(k)}")
    return changed


def main() -> int:
    cfg = load_config("config.yaml")
    proxy = str((cfg.get("proxy") or {}).get("http") or "http://127.0.0.1:7890")
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    device_id = str(uuid.uuid4())
    session = BrowserSession(cfg, proxy=proxy)
    flow = "oauth_create_account"

    result = {}
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
        page.goto("https://auth.openai.com/about-you", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        page.add_script_tag(url=f"https://sentinel.openai.com/sentinel/{sv}/sdk.js")
        page.wait_for_timeout(800)
        ok = page.evaluate("""() => !!(window.SentinelSDK && typeof window.SentinelSDK.init === 'function')""")
        print("SDK:", ok, flush=True)
        # init + token() 启动 collector
        page.evaluate(
            """async (flow) => { try { await window.SentinelSDK.init(flow);
                await window.SentinelSDK.token(flow); } catch (e) { return String(e); } return null; }""",
            flow)
        page.wait_for_timeout(1200)  # 等 collector 注册监听器

        # 基线
        prev = snap(page)
        result["baseline"] = prev
        print(f"\n=== 基线(se 后,无触发) ===", flush=True)
        for k, v in sorted(prev.items()):
            print(f"  {k} = {str(v)[:40]}", flush=True)

        # 事件序列(每类触发后采集,对比上一步)
        events = [
            ("pointermove", lambda: page.mouse.move(340, 220)),
            ("pointermove2", lambda: page.mouse.move(460, 310)),
            ("wheel", lambda: page.mouse.wheel(0, 120)),
            ("scroll", lambda: page.evaluate("window.scrollBy(0, 80)")),
            ("keydown", lambda: page.keyboard.press("Tab")),
            ("keydown2", lambda: page.keyboard.press("a")),
            ("click", lambda: page.mouse.click(500, 400)),
            ("paste", lambda: page.evaluate("window.dispatchEvent(new Event('paste', {bubbles:true}))")),
        ]
        for name, act in events:
            try:
                act()
            except Exception as e:
                result[name] = {"error": str(e)[:80]}
                print(f"\n{name}: ERROR {str(e)[:60]}", flush=True)
                continue
            page.wait_for_timeout(500)  # 等 collector 处理
            cur = snap(page)
            changed = diff(prev, cur)
            result[name] = cur
            print(f"\n=== {name} 后变化({len(changed)} 字段) ===", flush=True)
            for c in changed:
                print(f"  {c}", flush=True)
            prev = cur

        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()

    (ROOT / "data" / "so_event_map.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n结果已存 data/so_event_map.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

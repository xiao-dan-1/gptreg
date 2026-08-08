#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集真实浏览器 session observer 采集的 __oai_so_* 字段值（行为模型模板）。

流程: 加载 auth.openai.com + sdk → init → 真实行为(鼠标轨迹/滚动/按键 ~8s) → dump __oai_so_*
用法: python capture/capture_behavior_fields.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

FLOW = "oauth_create_account"
PROXY = "http://127.0.0.1:7890"


def main() -> int:
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": PROXY},
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        page.goto("https://auth.openai.com/about-you", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        page.add_script_tag(url="https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js")
        page.wait_for_timeout(800)

        # init 触发 collector 激活
        page.evaluate(
            """async (flow) => {
                try { if (window.SentinelSDK && typeof window.SentinelSDK.init === 'function') await window.SentinelSDK.init(flow); }
                catch (e) { return String(e); }
                return null;
            }""", FLOW)
        page.wait_for_timeout(1500)

        # 真实行为：鼠标轨迹（多段）+ 滚动 + 按键
        for _ in range(6):
            x0, y0 = 200 + _ * 40, 150 + _ * 30
            x1, y1 = x0 + 120, y0 + 60
            page.mouse.move(x0, y0)
            page.wait_for_timeout(60)
            page.mouse.move(x1, y1, steps=12)
            page.wait_for_timeout(80)
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(200)
        page.mouse.wheel(0, -100)
        page.wait_for_timeout(150)
        page.keyboard.press("Tab")
        page.wait_for_timeout(120)
        page.mouse.click(500, 400)
        page.wait_for_timeout(300)
        page.mouse.move(700, 500, steps=8)
        page.wait_for_timeout(500)

        # dump __oai_so_*（collector 采集的）
        fields = page.evaluate(
            """() => {
                const out = {};
                for (const k of Object.keys(window)) {
                    if (k.startsWith('__oai_so_')) {
                        const v = window[k];
                        out[k] = (v === null || v === undefined) ? null : (typeof v === 'object' ? JSON.stringify(v) : String(v));
                    }
                }
                return out;
            }""")
        json.dump(fields, open(out_dir / "browser_oai_so_fields.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("=== 真实浏览器 __oai_so_* 字段 ===")
        for k in sorted(fields):
            print(f"  {k} = {fields[k]!r}")
        ctx.close()
    print(f"\n已存 {out_dir / 'browser_oai_so_fields.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

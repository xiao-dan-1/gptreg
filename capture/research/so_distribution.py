#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集浏览器真实 __oai_so_* 字段分布：模拟自然行为，多次采集，分析字段值分布。

用于改进 snap_inject（第一性原理：注入符合真实分布的字段值，而非独立随机）。

用法: python capture/so_distribution.py [times]
"""
from __future__ import annotations

import json
import random
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
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
                out[k] = (typeof v === 'function') ? '[func]' : v;
            }
        }
        return out;
    }""")


def simulate_natural(page, rng: random.Random):
    """模拟自然行为：人类鼠标轨迹 + 点击 + 滚动 + 键盘。"""
    x, y = 400, 300
    for _ in range(25):
        x += rng.randint(-15, 15)
        y += rng.randint(-10, 10)
        x = max(50, min(1800, x))
        y = max(50, min(900, y))
        page.mouse.move(x, y, steps=rng.randint(1, 3))
        time.sleep(0.02 + rng.random() * 0.06)
    if rng.random() < 0.7:
        page.mouse.click(rng.randint(200, 800), rng.randint(150, 500))
        time.sleep(0.05)
    if rng.random() < 0.6:
        page.mouse.wheel(0, rng.randint(100, 400))
        time.sleep(0.05)
    if rng.random() < 0.5:
        page.keyboard.type("test")
        time.sleep(0.05)
    page.wait_for_timeout(300)


def main() -> int:
    times = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    cfg = load_config("config.yaml")
    proxy = str((cfg.get("proxy") or {}).get("http") or "http://127.0.0.1:7890")
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    session = BrowserSession(cfg, proxy=proxy)
    flow = "oauth_create_account"

    samples = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": proxy},
        )
        for n in range(times):
            rng = random.Random(uuid.uuid4().int)
            ctx = browser.new_context(
                user_agent=session.user_agent, locale="en-US",
                viewport={"width": 1920, "height": 1080},
            )
            page = ctx.new_page()
            page.context.add_cookies([{"name": "oai-did", "value": str(uuid.uuid4()), "domain": ".openai.com", "path": "/"}])
            try:
                page.goto("https://auth.openai.com/about-you", wait_until="domcontentloaded", timeout=45000)
            except Exception:
                ctx.close()
                continue
            page.wait_for_timeout(1200)
            for _ in range(3):
                try:
                    page.add_script_tag(url=f"https://sentinel.openai.com/sentinel/{sv}/sdk.js")
                    break
                except Exception:
                    time.sleep(1)
            page.wait_for_timeout(600)
            # init + token 启动 collector
            page.evaluate(
                """async (flow) => { try { await window.SentinelSDK.init(flow);
                    await window.SentinelSDK.token(flow); } catch (e) {} return null; }""", flow)
            page.wait_for_timeout(800)
            # 模拟自然行为
            simulate_natural(page, rng)
            page.wait_for_timeout(600)
            fields = snap(page)
            samples.append(fields)
            print(f"[{n+1}/{times}] 字段: {sum(1 for k in fields if k != '__oai_so_')} 个, "
                  f"i={fields.get('__oai_so_i')} k={fields.get('__oai_so_k')} "
                  f"s={str(fields.get('__oai_so_s'))[:14]} t0={str(fields.get('__oai_so_t0'))[:14]} "
                  f"cs={str(fields.get('__oai_so_cs'))[:14]} sp={str(fields.get('__oai_so_sp'))[:14]}", flush=True)
            ctx.close()
        browser.close()

    # 分析数值字段分布
    num_fields = set()
    for s in samples:
        for k, v in s.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                num_fields.add(k)
    print(f"\n=== 数值字段分布({len(num_fields)} 个) ===")
    for k in sorted(num_fields):
        vals = [s[k] for s in samples if isinstance(s.get(k), (int, float))]
        if not vals:
            continue
        mn, mx = min(vals), max(vals)
        mean = sum(vals) / len(vals)
        print(f"  {k}: min={mn:.1f} max={mx:.1f} mean={mean:.1f} n={len(vals)}")

    # 字段间关系
    print("\n=== 字段相关性 ===")
    for a, b in [('__oai_so_i', '__oai_so_k'), ('__oai_so_cs', '__oai_so_cs2'),
                 ('__oai_so_fs', '__oai_so_fs2'), ('__oai_so_sp', '__oai_so_s')]:
        va = [s.get(a) for s in samples if isinstance(s.get(a), (int, float))]
        vb = [s.get(b) for s in samples if isinstance(s.get(b), (int, float))]
        if va and vb:
            print(f"  {a} vs {b}: {[(round(x,1), round(y,1)) for x, y in zip(va[:3], vb[:3])]}")

    (ROOT / "data" / "so_distribution.json").write_text(
        json.dumps(samples, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n结果已存 data/so_distribution.json（{len(samples)} 组）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

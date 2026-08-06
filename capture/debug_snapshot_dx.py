#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""解密 snapshot_dx / collector_dx 字节码程序并反汇编，定位 TypeError 来源。

TypeError: Assignment to constant variable. 在 vm 执行 snapshot_dx 时发生。
本脚本从浏览器实时拿 challenge + request_p，解密 dx(key=request_p)，反汇编标环境读取。

用法: python capture/debug_snapshot_dx.py
"""
from __future__ import annotations

import base64
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402

from playwright.sync_api import sync_playwright


def b64d(s: str) -> bytes:
    s2 = str(s).replace("-", "+").replace("_", "/")
    return base64.b64decode(s2 + "=" * (-len(s2) % 4))


def rt(data: bytes, key: str) -> str:
    """Rt XOR 解密。"""
    out = []
    n = len(key)
    for i, c in enumerate(data):
        out.append(chr(c ^ ord(key[i % n])))
    return "".join(out)


def main() -> int:
    cfg = load_config("config.yaml")
    proxy = str((cfg.get("proxy") or {}).get("http") or "http://127.0.0.1:7890")
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    device_id = str(uuid.uuid4())
    session = BrowserSession(cfg, proxy=proxy)

    challenge = None
    request_p = ""
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
        page.evaluate(
            """async (flow) => { try { await window.SentinelSDK.token(flow); } catch (e) {} }""",
            "oauth_create_account")
        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()

    if not challenge or not request_p:
        print("未拿到 challenge/request_p")
        return 1
    so = challenge.get("so") or {}
    print(f"so keys: {list(so.keys())}")
    for name in ["collector_dx", "snapshot_dx"]:
        dx = so.get(name) or ""
        print(f"\n=== {name} len={len(dx)} ===")
        raw = b64d(dx)
        try:
            prog_s = rt(raw, request_p)
            prog = json.loads(prog_s)
            print(f"解密成功({request_p}): {len(prog)} 条指令")
            # 反汇编标环境读取
            env = set()
            for i, inst in enumerate(prog):
                if not isinstance(inst, list):
                    continue
                for x in inst[1:]:
                    if isinstance(x, str) and x in (
                        "screen", "navigator", "document", "localStorage", "performance",
                        "Math", "window", "history", "location", "Object", "Reflect",
                    ):
                        env.add(x)
            print(f"环境读取: {sorted(env)}")
            # 打印前 30 条
            for i, inst in enumerate(prog[:30]):
                print(f"  [{i:3d}] {json.dumps(inst, ensure_ascii=False)[:100]}")
            (ROOT / "data" / f"debug_{name}.json").write_text(
                json.dumps(prog, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"request_p 解密失败: {e}")
            # 试 key="13.08"(blob2 用)
            for key in ["13.08", ""]:
                try:
                    prog_s = rt(raw, key)
                    prog = json.loads(prog_s)
                    print(f"  key={key!r} 解密成功: {len(prog)} 条")
                    break
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

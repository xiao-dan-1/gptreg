#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""解码对比 browser so vs vm so，定位服务端校验的精确差异（第一性原理）。

同 challenge 下 browser 产 so + vm 产 so(snap_inject)，用 request_p 做 XOR key 解码，
对比字段值/结构，找「活 vs 死」的 so 编码差异。

用法: python capture/so_decode_compare.py
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
from gptreg.sentinel_quickjs import _run_action, _ensure_sdk, _fingerprint_payload, _quickjs_script  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402

from playwright.sync_api import sync_playwright

FLOW = "oauth_create_account"


def rt(data: bytes, key: str) -> str:
    """Rt XOR 解密。"""
    n = len(key)
    return "".join(chr(c ^ ord(key[i % n])) for i, c in enumerate(data))


def decode_so(so_val: str, key: str) -> str:
    s2 = so_val.replace("-", "+").replace("_", "/")
    raw = base64.b64decode(s2 + "=" * (-len(s2) % 4))
    return rt(raw, key)


def main() -> int:
    cfg = load_config("config.yaml")
    proxy = str((cfg.get("proxy") or {}).get("http") or "http://127.0.0.1:7890")
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    device_id = str(uuid.uuid4())
    session = BrowserSession(cfg, proxy=proxy)
    script = _quickjs_script()
    sdk_file = _ensure_sdk(session, sv, 60000)
    fp = _fingerprint_payload(cfg, device_id, sv)

    challenge = None
    request_p = ""
    browser_so_val = None
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
                    request_p = str(json.loads(body).get("p") or "")
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
        # 模拟自然行为
        for i in range(25):
            page.mouse.move(200 + i * 30, 150 + i * 20, steps=2)
            page.wait_for_timeout(40)
        page.mouse.click(400, 300)
        page.wait_for_timeout(300)
        # 产 t + so
        r = page.evaluate(
            """async (flow) => { try {
                const t = await window.SentinelSDK.token(flow);
                const so = typeof window.SentinelSDK.sessionObserverToken === 'function'
                    ? await window.SentinelSDK.sessionObserverToken(flow) : null;
                return JSON.stringify({ t, so });
            } catch (e) { return 'ERR: ' + String(e); } }""", FLOW)
        if not (isinstance(r, str) and r.startswith("ERR")):
            tj = json.loads(r)
            so_raw = tj.get("so")
            if so_raw:
                try:
                    browser_so_val = json.loads(so_raw).get("so") if isinstance(so_raw, str) else None
                except Exception:
                    browser_so_val = None
        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()

    if not challenge or not request_p:
        print("未拿到 challenge/request_p")
        return 1

    # vm solve(snap_inject)产 so
    payload = dict(fp)
    payload.update({"request_p": request_p, "challenge": challenge, "flow": FLOW, "skip_so": False,
                    "snap_inject": True})
    r = _run_action(script, sdk_file, "solve", payload, 60000)
    vm_so_raw = r.get("so") or ""
    vm_so_val = None
    try:
        vm_so_val = json.loads(vm_so_raw).get("so") if isinstance(vm_so_raw, str) else None
    except Exception:
        pass

    print(f"request_p len={len(request_p)} browser_so_len={len(browser_so_val or '')} vm_so_len={len(vm_so_val or '')}")
    print(f"browser so head: {str(browser_so_val)[:60]}")
    print(f"vm      so head: {str(vm_so_val)[:60]}")

    # 暴力 key 探测：找哪个 key 解出可读字段明文
    if browser_so_val:
        print("\n=== key 探测 ===")
        import re as _re
        cand_keys = [request_p, "", "13.08", "0", "1", "a", "x", "T", "key"]
        for _k in cand_keys:
            try:
                _d = decode_so(browser_so_val, _k)
                _n = _re.findall(r"-?\d+\.?\d*", _d[:1000])
                _print = "".join(chr(c) if 32 <= c < 127 else "." for c in _d.encode("latin1", "replace"))
                print(f"  key={_k!r}: len={len(_d)} nums={_n[:12]} printable={_print[:40]!r}")
            except Exception:
                pass

    # 解码对比(用 request_p 做 key)
    decs = {}
    for name, val in [("browser", browser_so_val), ("vm", vm_so_val)]:
        if not val:
            continue
        dec = decode_so(val, request_p)
        decs[name] = dec
        printable = "".join(chr(c) if 32 <= c < 127 else "." for c in dec.encode("latin1", "replace"))
        print(f"\n=== {name} so 解码(len={len(dec)}) ===")
        print(f"head80: {printable[:80]}")

    # 对比原始 so_val 的 base64 解码字节(不依赖 XOR key，定位字段编码差异)
    def b64raw(sv: str) -> bytes:
        s2 = sv.replace("-", "+").replace("_", "/")
        return base64.b64decode(s2 + "=" * (-len(s2) % 4))
    if browser_so_val and vm_so_val:
        b = b64raw(browser_so_val)
        v = b64raw(vm_so_val)
        n = min(len(b), len(v))
        # 简单差异定位：每 16 字节一段，统计差异字节数
        print(f"\n=== 差异分布(每 16B 一段, 对齐区 {n}B) ===")
        for seg_start in range(0, n, 16):
            seg_end = min(seg_start + 16, n)
            seg_b = b[seg_start:seg_end]
            seg_v = v[seg_start:seg_end]
            diff_cnt = sum(1 for x, y in zip(seg_b, seg_v) if x != y)
            if diff_cnt > 0:
                print(f"  [{seg_start:4d}-{seg_end:3d}] diff={diff_cnt:2d}/16  browser={bytes(seg_b).hex()[:32]}  vm={bytes(seg_v).hex()[:32]}")
        # 尾部额外(vm 多出的)
        if len(v) > n:
            extra = v[n:]
            ep = "".join(chr(c) if 32 <= c < 127 else "." for c in extra)
            print(f"\n  vm 尾部额外 {len(extra)}B: {ep[:60]!r}")
        print(f"\n  browser 总 {len(b)}B, vm 总 {len(v)}B, 差 {len(v)-len(b)}B")
    return 0


if __name__ == "__main__":
    sys.exit(main())

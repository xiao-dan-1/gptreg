#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集 quickjs 注册种子：浏览器一次性抓 __reactRouterContext/localStorage/字体测量真值。

用于「种子 + 重算」验证：把种子注入 quickjs 注册的 solve payload，测补全 t 是否改善存活。
用法: python capture/seed_quickjs_capture.py
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402

from playwright.sync_api import sync_playwright


def main() -> int:
    cfg = load_config("config.yaml")
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    proxy = str((cfg.get("proxy") or {}).get("http") or "http://127.0.0.1:7890")
    device_id = str(uuid.uuid4())
    session = BrowserSession(cfg, proxy=proxy)

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

    seed = {"proxy": proxy, "sv": sv, "font_params": {"fontFamily": fname, "fontSize": fsize}}
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
        page.wait_for_timeout(2500)

        seed["react_router_full"] = page.evaluate(
            """() => { try { return JSON.parse(JSON.stringify(globalThis.__reactRouterContext)); }
            catch (e) { return null; } }""")
        seed["ls_extra"] = page.evaluate(
            """() => { const m={}; for(let i=0;i<localStorage.length;i++)
            { const k=localStorage.key(i); m[k]=String(localStorage.getItem(k)); } return m; }""")
        seed["font_gbcr"] = page.evaluate(
            """(p) => { const d=document.createElement('div');
                d.style.position='fixed'; d.style.visibility='hidden';
                d.style.fontFamily=p.fontFamily; d.style.fontSize=p.fontSize; d.innerText=p.innerText;
                document.body.appendChild(d); const r=d.getBoundingClientRect(); document.body.removeChild(d);
                return {x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,left:r.left,right:r.right,bottom:r.bottom}; }""",
            {"fontFamily": fname, "fontSize": fsize, "innerText": ftext})
        ctx.close()
        browser.close()

    # 检查 rctx 里是否含 cf IP 字段（决定是否需 IP 一致）
    cf_hits = []
    rctx = seed.get("react_router_full")
    if isinstance(rctx, dict):
        def walk(n, path=""):
            if isinstance(n, dict):
                for k, v in n.items():
                    if "cf" in k.lower() or "clientBootstrap" in k:
                        cf_hits.append(f"{path}.{k}={json.dumps(v, ensure_ascii=False)[:80]}")
                    walk(v, f"{path}.{k}")
        walk(rctx)
    seed["_cf_hits"] = cf_hits

    out = ROOT / "data" / "seed_quickjs.json"
    out.write_text(json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"种子已存 {out}", flush=True)
    print(f"rctx_full size={len(json.dumps(rctx, ensure_ascii=False)) if rctx else 'null'} "
          f"ls={len(seed.get('ls_extra') or {})}键 font={seed.get('font_gbcr')}", flush=True)
    print(f"rctx 里 cf/clientBootstrap 命中: {cf_hits if cf_hits else '无'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

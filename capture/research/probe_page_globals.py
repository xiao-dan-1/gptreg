#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针:真浏览器 auth.openai.com 页面的 React 全局 + localStorage + cf 字段。

blob2 程序读取:
  - window.__reactRouterContext.state.root.clientBootstrap.cfConnectingIp/cfIpCity/userRegion/cfIpLatitude/cfIpLongitude
  - Object.keys(localStorage)(含 SDK 自写键)
  - 字体渲染测量(div innerText + getBoundingClientRect)

本脚本用 Playwright 打开 about-you,采集这些真实值,判断 vm 能否注入。
用法: python capture/probe_page_globals.py
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402

from playwright.sync_api import sync_playwright


def main() -> int:
    cfg = load_config("config.yaml")
    proxy = str((cfg.get("proxy") or {}).get("http") or "http://127.0.0.1:7890")
    device_id = str(uuid.uuid4())

    session = BrowserSession(cfg, proxy=proxy)
    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": proxy},
        )
        ctx = browser.new_context(
            user_agent=session.user_agent,
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        page.context.add_cookies([{"name": "oai-did", "value": device_id, "domain": ".openai.com", "path": "/"}])
        page.goto("https://auth.openai.com/about-you", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)

        # 1) __reactRouterContext 结构 + 全 loaderData 树里搜 clientBootstrap / cf 字段
        rctx = page.evaluate("""() => {
            const r = globalThis.__reactRouterContext;
            if (!r) return { present: false };
            const st = r && r.state;
            // loaderData 通常是 { root: {...}, routeId: {...} } —— 深度搜索 clientBootstrap / cf*
            const hits = [];
            const walk = (node, path) => {
                if (node == null) return;
                if (typeof node !== 'object') return;
                for (const k of Object.keys(node)) {
                    const v = node[k];
                    const p = path ? path + '.' + k : k;
                    if (/cf/i.test(k) || /bootstrap/i.test(k)) {
                        hits.push({ path: p, value: typeof v === 'object' ? JSON.stringify(v).slice(0, 500) : String(v) });
                    }
                    if (v && typeof v === 'object' && path.split('.').length < 8) walk(v, p);
                }
            };
            walk(st, 'state');
            // 全 window 深度 2 搜 cf 字段
            const wHits = [];
            const walkWin = (node, path, depth) => {
                if (depth > 2 || node == null || typeof node !== 'object') return;
                for (const k of Object.keys(node)) {
                    if (/^(_cf|cf|cloudflare)/i.test(k)) {
                        wHits.push({ path: path ? path + '.' + k : k, value: String(node[k]).slice(0, 200) });
                    }
                    if (typeof node[k] === 'object' && path.split('.').length < 2) walkWin(node[k], k, depth + 1);
                }
            };
            try { walkWin(globalThis, '', 0); } catch (e) { /* ignore */ }
            return {
                present: true,
                stateKeys: st ? Object.keys(st).slice(0, 30) : null,
                loaderDataKeys: (st && st.loaderData) ? Object.keys(st.loaderData) : null,
                cfBootstrapHits: hits,
                windowCfHits: wHits,
            };
        }""")
        out["__reactRouterContext"] = rctx

        # 2) localStorage 全量
        ls = page.evaluate("""() => {
            const m = {};
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                m[k] = String(localStorage.getItem(k)).slice(0, 300);
            }
            return m;
        }""")
        out["localStorage"] = ls

        # 3) 字体渲染测量参考值:程序用 blob2 里的 fontFamily/fontSize/innerText 串,
        #    从 data/blob2_program.json 动态提取,避免手打 unicode 组合字符不一致。
        blob2 = json.loads((ROOT / "data" / "blob2_program.json").read_text(encoding="utf-8"))
        fname = fontsize = ftext = None
        for inst in blob2:
            if not isinstance(inst, list):
                continue
            if len(inst) >= 3 and inst[1] == "fontFamily" and isinstance(inst[2], str):
                fname = inst[2]
            if len(inst) >= 3 and inst[1] == "fontSize" and isinstance(inst[2], str):
                fontsize = inst[2]
            if len(inst) >= 3 and inst[1] == "innerText" and isinstance(inst[2], str):
                ftext = inst[2]
        fname = fname or "Lucida Sans Unicode"
        fontsize = fontsize or "14px"
        ftext = ftext or ""
        out["font_params"] = {"fontFamily": fname, "fontSize": fontsize, "innerText": ftext,
                              "innerText_esc": ftext.encode("unicode_escape").decode()}
        fnt = page.evaluate(
            """(p) => {
                const div = document.createElement('div');
                div.style.position = 'fixed';
                div.style.visibility = 'hidden';
                div.style.fontFamily = p.fontFamily;
                div.style.fontSize = p.fontSize;
                div.innerText = p.innerText;
                document.body.appendChild(div);
                const r = div.getBoundingClientRect();
                document.body.removeChild(div);
                return { x: r.x, y: r.y, width: r.width, height: r.height, top: r.top,
                         left: r.left, right: r.right, bottom: r.bottom };
            }""", {"fontFamily": fname, "fontSize": fontsize, "innerText": ftext})
        out["font_measure"] = fnt

        # 4) window 键采样(SDK 随机采样用的键空间) + cf/bootstrap/react 相关键
        keys = page.evaluate("""() => {
            const all = Object.getOwnPropertyNames(globalThis);
            const interesting = all.filter(k => /cf|bootstrap|react|router|sentinel|statsig|_cf/i.test(k));
            return { sample: all.slice(0, 200), interesting };
        }""")
        out["window_keys"] = keys

        # 5) document.title / readyState(诊断页面加载态)
        out["page"] = page.evaluate("""() => ({ title: document.title, readyState: document.readyState,
            url: location.href })""")

        # 6) 抓 HTML 源码,搜索 clientBootstrap / cfConnectingIp 是否 SSR 在 HTML 里
        html = page.content()
        for needle in ["clientBootstrap", "cfConnectingIp", "cfIpCity", "__reactRouterContext",
                       "userRegion", "cfIpLatitude", "cfIpLongitude", "statsig"]:
            idx = html.find(needle)
            out.setdefault("html_hits", {})[needle] = (
                html[max(0, idx - 120):idx + 240] if idx >= 0 else None
            )
        out["html_len"] = len(html)
        ctx.close()
        browser.close()

    (ROOT / "data" / "page_globals_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # 打印关键结论
    cb = out["__reactRouterContext"]
    print("__reactRouterContext:", "present" if cb and cb.get("present") else "ABSENT")
    if cb and cb.get("present"):
        print("  stateKeys:", (cb.get("stateKeys") or [])[:12])
        print("  clientBootstrapKeys:", cb.get("clientBootstrapKeys"))
        print("  cfSample:", json.dumps(cb.get("cfSample"), ensure_ascii=False))
    print("localStorage keys:", list(out["localStorage"].keys()))
    print("font_measure:", out["font_measure"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

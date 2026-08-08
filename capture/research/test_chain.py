#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试链式代理隧道机制: resolve_proxy 建 7890→cliproxy 隧道, 验证出口 IP + OpenAI 连通性。

用法: python capture/test_chain.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402


def main() -> int:
    cfg = load_config()
    r = resolve_proxy(cfg)  # 无 override → 用 dynamic + 链式
    print(f"label:        {r.label()}")
    print(f"session_url:  {r.session_url}")
    print(f"upstream:     {r.upstream_url[:80]}")
    print(f"region/sid:   {r.region}/{r.sid}")
    if not r.session_url:
        print("[!] session_url 空(直连?)")
        return 1

    import curl_cffi.requests as cr
    proxies = {"http": r.session_url, "https": r.session_url}
    try:
        resp = cr.get("https://api.ipify.org", proxies=proxies,
                      timeout=25, impersonate="chrome", allow_redirects=True)
        print(f"\n[出口 IP] api.ipify.org -> {resp.status_code}: {resp.text.strip()[:60]}")
    except Exception as exc:
        print(f"[出口 IP] 异常: {type(exc).__name__}: {str(exc)[:120]}")
    try:
        resp2 = cr.get("https://auth.openai.com/", proxies=proxies,
                       timeout=25, impersonate="chrome", allow_redirects=True)
        print(f"[auth.openai.com] -> {resp2.status_code} ({(resp2.text or '')[:60]})")
    except Exception as exc:
        print(f"[auth.openai.com] 异常: {type(exc).__name__}: {str(exc)[:120]}")
    try:
        resp3 = cr.get("https://chatgpt.com/", proxies=proxies,
                       timeout=25, impersonate="chrome", allow_redirects=True)
        print(f"[chatgpt.com] -> {resp3.status_code}")
    except Exception as exc:
        print(f"[chatgpt.com] 异常: {type(exc).__name__}: {str(exc)[:120]}")

    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

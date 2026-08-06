#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实测各 flow 的 /req 是否要求 so:验证 username_password_create(密码模式)能否绕过 so。

背景:2026-07-12 研究笔记显示 username_password_create flow 无 so 字段(只要 pow+turnstile)。
若当前仍成立,密码模式 = 纯协议(不经浏览器)可能直接存活,绕过整个 vm so 死局。

用法: python capture/verify_pwd_flow.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.auth import request_sentinel  # noqa: E402

FLOWS = ["authorize_continue", "oauth_create_account", "username_password_create"]


def main() -> int:
    cfg = load_config()
    resolved = resolve_proxy(cfg)
    sess = BrowserSession(cfg, proxy=resolved.session_url)

    print(f"代理: {resolved.label()} via {resolved.session_url}\n")
    for flow in FLOWS:
        try:
            data = request_sentinel(sess, flow)
        except Exception as exc:
            print(f"[{flow:<26}] ERROR {type(exc).__name__}: {str(exc)[:100]}")
            continue
        so = data.get("so") if isinstance(data, dict) else None
        has_so_field = isinstance(so, dict) and bool(so.get("required"))
        so_required = bool(so.get("required")) if isinstance(so, dict) else None
        cdx = (so or {}).get("collector_dx") or ""
        sdx = (so or {}).get("snapshot_dx") or ""
        pow_blk = data.get("proofofwork") or {}
        ts_blk = data.get("turnstile") or {}
        print(f"[{flow:<26}] so_required={str(so_required):<5} "
              f"collector_dx={len(cdx)} snapshot_dx={len(sdx)} "
              f"pow_difficulty={pow_blk.get('difficulty')} "
              f"turnstile_dx={len(ts_blk.get('dx') or '')} keys={sorted(k for k in data.keys())[:12]}")
    resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

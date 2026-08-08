#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探测动态代理链路(cliproxy via chain_via)出口 IP 与类型。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config
from gptreg.proxyutil import resolve_proxy, probe_proxy

cfg = load_config()
rp = resolve_proxy(cfg)
print("session:", rp.session_url or "(直连)")
print("upstream:", rp.upstream_url or "(无)")
print("label:", rp.label(), "| region:", rp.region, "| sid:", rp.sid)
try:
    info = probe_proxy(rp.session_url, timeout=25)
    print("status:", info.get("status"))
    print("ip:", info.get("ip"))
    print("ipinfo:", info.get("ipinfo"))
except Exception as exc:
    print("探测失败:", exc)
finally:
    rp.close()

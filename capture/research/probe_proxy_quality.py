#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实测动态代理质量: 换sid是否换IP / 连接稳定性 / 速度 / 住宅or数据中心。"""
import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config
from gptreg.proxyutil import build_dynamic_proxy, set_sid, random_sid, resolve_proxy, proxy_label
from curl_cffi import requests as cr

cfg = load_config()

def check(proxy_url: str, label: str) -> dict:
    """经链式隧道访问 ipinfo, 返回出口信息。"""
    rp = resolve_proxy(cfg, override=proxy_url)
    t0 = time.time()
    try:
        r = cr.get("https://ipinfo.io/json", timeout=25, impersonate="chrome142",
                   proxies={"http": rp.session_url, "https": rp.session_url})
        dt = time.time() - t0
        j = r.json() if r.status_code == 200 else {}
        info = {
            "label": label, "http": r.status_code, "time_s": round(dt, 2),
            "ip": j.get("ip"), "city": j.get("city"), "org": j.get("org"),
            "region": j.get("region"),
        }
    except Exception as e:
        info = {"label": label, "error": f"{type(e).__name__}: {str(e)[:60]}", "time_s": round(time.time()-t0, 2)}
    finally:
        rp.close()
    return info

print("=== 动态代理实测 ===")
# 1) 同 sid 粘性(固定模板) 连续 3 次, 看 IP 是否稳定
print("\n[1] 固定 sid(粘性) 连续 3 次:")
base = build_dynamic_proxy(cfg)
for i in range(3):
    info = check(base, f"同sid #{i+1}")
    print(f"  {info}")

# 2) 换 3 个不同 sid, 看 IP 是否变化
print("\n[2] 换 3 个不同 sid(看 IP 是否真变):")
seen_ips = set()
for i in range(3):
    new = set_sid(base, sid=random_sid(8), sid_len=8)
    info = check(new, f"新sid #{i+1}")
    seen_ips.add(info.get("ip"))
    print(f"  {info}")
print(f"  不同 IP 数: {len(seen_ips)}")

# 3) 直连对比(无代理)
print("\n[3] 直连对比:")
info = check("", "直连")
print(f"  {info}")

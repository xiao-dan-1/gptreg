#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 rotate_sid=true 规避批量风控: 连续换 IP 访问 OpenAI 接口, 观察是否 403。

对比:
  - rotate_sid=false(固定 IP): 之前实测 4-5 个账号后 accounts/check 403 blocked
  - rotate_sid=true(每账号换 IP): 应持续 200, 不触发 WAF 拦截
"""
import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config
from gptreg.proxyutil import build_dynamic_proxy, resolve_proxy, random_sid, set_sid
from gptreg.session import BrowserSession

cfg = load_config()
N = int(sys.argv[1]) if len(sys.argv) > 1 else 6  # 连续访问次数

print(f"验证 rotate_sid={cfg['proxy']['dynamic'].get('rotate_sid')}: 连续 {N} 次换 IP 访问 OpenAI")
blocked = 0      # 403 + HTML = 真被风控(WAF 拦)
ok = 0           # 200 正常
tunnel_err = 0   # TLS/隧道偶发错误(非风控)
for i in range(N):
    rp = resolve_proxy(cfg)  # 每次新 sid(rotate_sid=true)
    sess = BrowserSession(cfg, proxy=rp.session_url)
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h.pop("content-type", None)
    try:
        r = sess.get("https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                     headers=h, timeout=20)
        body = (r.text or "")[:60]
        if r.status_code == 403 and "<html" in body.lower():
            blocked += 1
            tag = "BLOCKED(风控)"
        else:
            ok += 1
            tag = "OK"
        print(f"  #{i+1} sid={rp.sid or '?'} http={r.status_code} [{tag}] {body[:40]}")
    except Exception as e:
        tunnel_err += 1
        print(f"  #{i+1} sid={rp.sid or '?'} 隧道错误 {type(e).__name__} {str(e)[:40]}")
    finally:
        rp.close()
    time.sleep(1)

print(f"\n结果: OK {ok}/{N}  风控 {blocked}/{N}  隧道错误 {tunnel_err}/{N}")
print("结论:", "rotate_sid=true 生效, 未被 IP 风控" if blocked == 0 else "仍有被风控迹象")

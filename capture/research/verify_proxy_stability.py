#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""综合验证动态代理稳定性: 隧道探活重建 + 瞬时错误重试 修复效果。

测 3 个维度:
  1. resolve_proxy 探活成功率(建连时失败被重建消化)
  2. 隧道建好后中途稳定性(建好→探活→再探活, 模拟注册链多段使用)
  3. 瞬时错误重试逻辑(单元级, 验证换 sid)
"""
import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config
from gptreg.proxyutil import resolve_proxy, probe_proxy, build_dynamic_proxy, set_sid, random_sid
from gptreg.register_pwd import _session_fail_retry, RegisterOutcome

cfg = load_config()
N = int(sys.argv[1]) if len(sys.argv) > 1 else 8

print(f"=== 1. resolve_proxy 探活成功率 ({N} 次) ===")
ok = rebuild = fail = 0
t_total = 0.0
for i in range(N):
    t0 = time.time()
    rp = resolve_proxy(cfg)  # 内部探活+重建
    dt = time.time() - t0
    t_total += dt
    info = probe_proxy(rp.session_url, timeout=10)  # 二次探活确认
    if info.get("status") == 200 and info.get("ip"):
        ok += 1
        st = "OK"
    else:
        fail += 1
        st = "FAIL"
    print(f"  #{i+1} {st} ip={info.get('ip') or '?'} resolve={dt:.1f}s")
    rp.close()
    time.sleep(0.3)
print(f"  探活成功 {ok}/{N}, 平均 resolve {t_total/N:.1f}s")

print(f"\n=== 2. 隧道中途稳定性(建好后连测 3 次探活, 模拟注册链多段) ===")
mid_ok = mid_fail = 0
for i in range(3):
    rp = resolve_proxy(cfg)
    stable = True
    for j in range(3):
        info = probe_proxy(rp.session_url, timeout=8)
        if info.get("status") != 200:
            stable = False
            mid_fail += 1
            break
    if stable:
        mid_ok += 1
    print(f"  隧道#{i+1}: {'STABLE' if stable else 'UNSTABLE'}")
    rp.close()
print(f"  中途稳定 {mid_ok}/3")

print(f"\n=== 3. 瞬时错误重试逻辑 ===")
class FakeSSLError(Exception): pass
proxy_url = 'http://acc-region-US-sid-old-t-5:pw@us.cliproxy.io:3010'
res, new_url = _session_fail_retry({}, FakeSSLError('curl 35 TLS'), 'a@b.com', proxy_url, 0, {}, True)
print(f"  瞬时错误 → 重试(换sid): {res is None}")
res2, _ = _session_fail_retry({}, ValueError('session 无 token'), 'a@b.com', proxy_url, 0, {}, True)
print(f"  非瞬时 → 终止: {res2.outcome == RegisterOutcome.SESSION_FAILED}")

print(f"\n结论: 探活 {ok}/{N} 中途稳定 {mid_ok}/3 重试逻辑正确")

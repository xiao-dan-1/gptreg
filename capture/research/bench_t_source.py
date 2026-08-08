#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""基准:vm 产 t vs browser 产 t 的耗时差距(同流程、同代理)。

用法: python capture/bench_t_source.py [rounds]
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg import browser_sentinel  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402

FLOW = "oauth_create_account"


def bench_browser(cfg, proxy) -> float:
    t0 = time.time()
    r = browser_sentinel.harvest_browser_sentinel(
        cfg, flow=FLOW, device_id=str(uuid.uuid4()), proxy=proxy, headless=True, timeout_s=90,
    )
    dt = time.time() - t0
    ok = r.get("ok") and r.get("token")
    print(f"  browser: ok={ok} t_len={len(r.get('token') or '') if r.get('token') else 0} "
          f"so_len={r.get('so_len')} err={r.get('error')} 耗时={dt:.1f}s", flush=True)
    if not ok:
        return float("nan")
    return dt


def bench_vm(cfg, proxy) -> float:
    sess = BrowserSession(cfg, proxy=proxy)
    t0 = time.time()
    try:
        token, so_header = get_sentinel_token_via_quickjs(
            sess, str(uuid.uuid4()), flow=FLOW, cfg=cfg, timeout_ms=120000,
        )
    except Exception as exc:
        print(f"  vm: 异常 {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        return float("nan")
    dt = time.time() - t0
    t_len = len(str(token))
    so_len = len(so_header or "")
    print(f"  vm: ok=1 t_len={t_len} so_len={so_len} 耗时={dt:.1f}s", flush=True)
    try:
        sess.close()
    except Exception:
        pass
    return dt


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    cfg = load_config("config.yaml")
    resolved = resolve_proxy(cfg)
    proxy = resolved.session_url
    print(f"代理: {proxy}")

    print("\n=== browser 产 t ===")
    b_times = [bench_browser(cfg, proxy) for _ in range(rounds)]
    print("\n=== vm 产 t (requirements + /req + solve) ===")
    v_times = [bench_vm(cfg, proxy) for _ in range(rounds)]

    def avg(xs):
        xs = [x for x in xs if x == x]
        return sum(xs) / len(xs) if xs else float("nan")

    b_avg, v_avg = avg(b_times), avg(v_times)
    print("\n==== 汇总 ====")
    print(f"browser 产 t 平均: {b_avg:.1f}s  ({rounds} 次)")
    print(f"vm     产 t 平均: {v_avg:.1f}s  ({rounds} 次)")
    if b_avg == b_avg and v_avg == v_avg:
        print(f"vm 比 browser 快: {b_avg / v_avg:.1f}x")
        print(f"browser 比 vm 慢: {b_avg - v_avg:.1f}s")
    resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

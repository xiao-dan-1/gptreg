#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量测活所有 2FA 账号, 定期换出口 IP 规避风控(403 blocked)。

背景: 连续 accounts/check 同一 IP 会被 OpenAI WAF 拦成 403 HTML(blocked),
被误判为账号死亡(实际 IP 风控)。本脚本每 ROTATE_EVERY 个换一次 sid。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config
from gptreg.health import check_account_health
from gptreg.session import BrowserSession
from gptreg.proxyutil import build_dynamic_proxy, resolve_proxy, set_sid, random_sid


def _load_2fa_accounts() -> list[dict]:
    """accounts.jsonl 里带 totp_secret + access_token 的记录(按时间降序)。"""
    recs = []
    p = ROOT / "output" / "accounts.jsonl"
    if not p.exists():
        return recs
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("totp_secret") and d.get("access_token"):
            recs.append(d)
    recs.sort(key=lambda d: str(d.get("saved_at") or d.get("updated_at") or ""), reverse=True)
    return recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只测最近 N 个(0=全部)")
    ap.add_argument("--rotate", type=int, default=8, help="每 N 个换一次出口 IP")
    ap.add_argument("--email", default="", help="逗号分隔指定邮箱(覆盖默认全部)")
    args = ap.parse_args()

    if args.email:
        emails = {e.strip() for e in args.email.split(",") if e.strip()}
        accounts = [d for d in _load_2fa_accounts() if d.get("email") in emails]
    else:
        accounts = _load_2fa_accounts()
    if args.limit:
        accounts = accounts[: args.limit]
    print(f"测活 {len(accounts)} 个 2FA 账号(每 {args.rotate} 个换出口 IP):")

    cfg = load_config()
    results: list[tuple[str, str, int | None]] = []
    resolved = None
    sess = None
    t_start = time.time()

    for i, d in enumerate(accounts, 1):
        # 定期换出口 IP(规避 403 blocked 风控)
        if resolved is None or (i - 1) % args.rotate == 0:
            if resolved is not None:
                resolved.close()
            new_url = set_sid(build_dynamic_proxy(cfg), sid=random_sid(8), sid_len=8)
            resolved = resolve_proxy(cfg, override=new_url)
            sess = BrowserSession(cfg, proxy=resolved.session_url)
            print(f"  [轮换{i}] 新出口 sid={resolved.sid or '?'}")

        email = d.get("email", "?")
        age_h = _age_h(d.get("saved_at") or d.get("updated_at") or "")
        t0 = time.time()
        try:
            r = check_account_health(sess, d.get("access_token"))
            st = r.get("status")
            http = r.get("http")
            results.append((email, st, http))
            print(f"  [{i}/{len(accounts)}] {email:42s} age={age_h:>6s} -> {st} http={http}")
        except Exception as exc:
            results.append((email, "error", None))
            print(f"  [{i}/{len(accounts)}] {email:42s} -> 异常 {type(exc).__name__}: {str(exc)[:40]}")
        dt = time.time() - t0
        if dt > 3:
            print(f"      [耗时] 本账号 {dt:.1f}s")
        time.sleep(0.5)

    if resolved is not None:
        resolved.close()

    ok = sum(1 for _, s, _ in results if s == "ok")
    dead = sum(1 for _, s, _ in results if s in ("invalidated", "deactivated"))
    other = len(results) - ok - dead
    print(f"\n存活: {ok}/{len(results)}  吊销/封禁: {dead}  其他(error/限流): {other}")
    print(f"总耗时 {time.time()-t_start:.1f}s")
    return 0


def _age_h(ts: str) -> str:
    """时间戳 → 存活时长(小时/天)。"""
    try:
        from datetime import datetime
        t = datetime.fromisoformat(ts)
        h = (time.time() - t.timestamp()) / 3600
        if h >= 24:
            return f"{h/24:.0f}d"
        return f"{h:.1f}h"
    except Exception:
        return "?"


if __name__ == "__main__":
    raise SystemExit(main())

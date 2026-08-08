#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""账号聚合视图: accounts.jsonl 资产总览。

回答"当前有多少可用账号/已吊销/待补 2FA/即将过期", 定位可用资产、识别废弃账号。

用法:
    python capture/account_overview.py              # 全部汇总
    python capture/account_overview.py --alive      # 只列存活 2FA 账号
    python capture/account_overview.py --dead       # 只列吊销/失效账号
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _ts(d: dict) -> str:
    return str(d.get("saved_at") or d.get("updated_at") or "")


def _age(ts: str) -> str:
    try:
        t = datetime.fromisoformat(ts)
        h = (time.time() - t.timestamp()) / 3600
        if h < 1:
            return f"{h*60:.0f}m"
        if h < 24:
            return f"{h:.1f}h"
        return f"{h/24:.0f}d"
    except Exception:
        return "?"


def _load() -> list[dict]:
    p = ROOT / "output" / "accounts.jsonl"
    recs = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    return recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alive", action="store_true", help="只列存活 2FA 账号")
    ap.add_argument("--dead", action="store_true", help="只列吊销/失效账号")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    recs = _load()
    if not recs:
        print("accounts.jsonl 无记录")
        return 0

    # 维度分类
    totp = [d for d in recs if d.get("totp_secret")]
    no_totp = [d for d in recs if not d.get("totp_secret")]
    health = Counter(d.get("health_status") or "never_checked" for d in recs)
    reg_status = Counter(str(d.get("status")) for d in recs)
    by_day = Counter((_ts(d) or "?")[:10] for d in recs)

    print(f"账号总数: {len(recs)}")
    print(f"  带 TOTP(真 2FA): {len(totp)}   未激活 TOTP: {len(no_totp)}")
    print(f"\n注册状态: " + "  ".join(f"{k}={v}" for k, v in sorted(reg_status.items())))
    print(f"\n存活状态(health_status, 测活回写):")
    for k in ("ok", "invalidated", "deactivated", "error", "never_checked"):
        print(f"  {k:12s}: {health.get(k, 0)}")
    print(f"\n按注册日: " + "  ".join(f"{d}={c}" for d, c in sorted(by_day.items(), reverse=True)))

    # 2FA 账号存活明细
    alive = [d for d in totp if d.get("health_status") == "ok"]
    dead = [d for d in totp if d.get("health_status") in ("invalidated", "deactivated")]
    unknown = [d for d in totp if d.get("health_status") not in ("ok", "invalidated", "deactivated")]
    print(f"\n2FA 账号: 存活 {len(alive)} / 吊销 {len(dead)} / 未测活 {len(unknown)}")

    # 按号源分组存活率(定位号源可靠性, 如 CloudMail 存活率低)
    src_ok: Counter = Counter()
    src_tot: Counter = Counter()
    for d in totp:
        mt = _mail_type(d)
        src_tot[mt] += 1
        if d.get("health_status") == "ok":
            src_ok[mt] += 1
    print("\n按号源存活率(2FA 账号):")
    for mt in sorted(src_tot):
        rate = src_ok[mt] / src_tot[mt] * 100 if src_tot[mt] else 0
        print(f"  {mt:10s}: 存活 {src_ok[mt]}/{src_tot[mt]} ({rate:.0f}%)")

    if args.alive:
        _print_list(alive, "存活 2FA 账号", args.limit)
    if args.dead:
        _print_list(dead, "吊销/失效账号", args.limit)
    return 0


def _mail_type(d: dict) -> str:
    """号源类型: 优先 mail_type, 否则域名推断。"""
    mt = str(d.get("mail_type") or "")
    if mt:
        return mt
    email = (d.get("email") or "").lower()
    if email.endswith(("icloud.com", "me.com")):
        return "icloud"
    if "xdauv" in email:
        return "cloudmail"
    if email.endswith("outlook.com"):
        return "ms_oauth"
    return "?"


def _print_list(recs: list[dict], title: str, limit: int) -> None:
    if not recs:
        print(f"\n{title}: 无")
        return
    print(f"\n{title} ({len(recs)}):")
    for d in sorted(recs, key=_ts, reverse=True):
        if limit and limit <= 0:
            break
        if limit:
            limit -= 1
        email = d.get("email", "?")
        so = d.get("sentinel_obs") or {}
        print(f"  {email:44s} {_ts(d)[:19]} age={_age(_ts(d)):>5s} "
              f"totp={bool(d.get('totp_secret'))} health={d.get('health_status','?')}")


if __name__ == "__main__":
    raise SystemExit(main())

"""overview: 账号聚合视图(accounts.jsonl 资产总览)。

从 capture/tools/account_overview.py 收编。回答"当前有多少可用/已吊销/待补 2FA",
定位可用资产、识别废弃账号、看号源存活率。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from gptreg.account_store import load_accounts, mail_type_of
from gptreg.commands.common import age_str, ts_str


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("overview", help="账号资产总览(存活/吊销/号源存活率)")
    p.add_argument("--alive", action="store_true", help="只列存活 2FA 账号")
    p.add_argument("--dead", action="store_true", help="只列吊销/失效账号")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=run)


def run(cfg: dict[str, Any], args) -> int:
    recs = load_accounts(cfg)
    if not recs:
        print("accounts.jsonl 无记录")
        return 0

    # 维度分类
    totp = [d for d in recs if d.get("totp_secret")]
    no_totp = [d for d in recs if not d.get("totp_secret")]
    health = Counter(d.get("health_status") or "never_checked" for d in recs)
    reg_status = Counter(str(d.get("status")) for d in recs)
    by_day = Counter((ts_str(d) or "?")[:10] for d in recs)

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
        mt = mail_type_of(d)
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


def _print_list(recs: list[dict], title: str, limit: int) -> None:
    if not recs:
        print(f"\n{title}: 无")
        return
    print(f"\n{title} ({len(recs)}):")
    for d in sorted(recs, key=ts_str, reverse=True):
        if limit and limit <= 0:
            break
        if limit:
            limit -= 1
        email = d.get("email", "?")
        so = d.get("sentinel_obs") or {}
        print(f"  {email:44s} {ts_str(d)[:19]} age={age_str(ts_str(d)):>5s} "
              f"totp={bool(d.get('totp_secret'))} health={d.get('health_status','?')} "
              f"mode={so.get('challenge_mode') or so.get('challenge', '?')}")

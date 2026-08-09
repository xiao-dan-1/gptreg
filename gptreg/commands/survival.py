"""survival: 批量测活 2FA 账号(定期换出口 IP 规避 403 风控)。

从 capture/tools/check_survival_batch.py 收编(带回 --source 过滤), 记录回写 health_status。
背景: 连续 accounts/check 同一 IP 会被 OpenAI WAF 拦成 403 HTML(blocked),
被误判为账号死亡(实际 IP 风控)。每 ROTATE_EVERY 个换一次 sid。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from gptreg.account_store import load_accounts, mail_type_of, update_account_health
from gptreg.commands.common import RotatingSession, age_h, age_h_float
from gptreg.health import check_account_health


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("survival", help="批量测活 2FA 账号(定期换 IP, 回写 health)")
    p.add_argument("--limit", type=int, default=0, help="只测最近 N 个(0=全部)")
    p.add_argument("--rotate", type=int, default=8, help="每 N 个换一次出口 IP")
    p.add_argument("--email", default="", help="逗号分隔指定邮箱(覆盖默认全部)")
    p.add_argument("--source", default="", help="只测指定号源(ms_oauth/icloud/cloudmail), 与 --email 叠加")
    p.set_defaults(func=run)


def _load_2fa_accounts(cfg: dict[str, Any]) -> list[dict]:
    """accounts.jsonl 里带 totp_secret + access_token 的记录(按时间降序)。"""
    recs = [d for d in load_accounts(cfg) if d.get("totp_secret") and d.get("access_token")]
    recs.sort(key=lambda d: str(d.get("saved_at") or d.get("updated_at") or ""), reverse=True)
    return recs


def run(cfg: dict[str, Any], args) -> int:
    accounts = _load_2fa_accounts(cfg)
    if args.source:
        accounts = [d for d in accounts if mail_type_of(d) == args.source]
    if args.email:
        emails = {e.strip() for e in args.email.split(",") if e.strip()}
        accounts = [d for d in accounts if d.get("email") in emails]
    if args.limit:
        accounts = accounts[: args.limit]
    print(f"测活 {len(accounts)} 个 2FA 账号(每 {args.rotate} 个换出口 IP):")

    results: list[tuple[str, str, str, int | None, float]] = []  # (email, type, status, http, age_h)
    rot = RotatingSession(cfg, rotate=args.rotate)
    try:
        for i, d in enumerate(accounts, 1):
            sess = rot.get(i)  # 每 rotate 个重建(换出口 IP)
            if rot.rotated:
                print(f"  [轮换{i}] 新出口 sid={rot.sid or '?'}")

            email = d.get("email", "?")
            mtype = mail_type_of(d)
            age = age_h_float(d.get("saved_at") or d.get("updated_at") or "")
            age_s = age_h(age)
            try:
                r = check_account_health(sess, d.get("access_token"))
                st = r.get("status")
                http = r.get("http")
                results.append((email, mtype, st, http, age))
                if st == "error":
                    # error 多为代理/网络抖动(非账号死亡), 显示 detail 便于区分是否需重测
                    det = str(r.get("detail") or r.get("body") or "")[:70]
                    print(f"  [{i}/{len(accounts)}] {mtype:9s} {email:42s} age={age_s:>6s} -> error http=None [{det}]")
                else:
                    print(f"  [{i}/{len(accounts)}] {mtype:9s} {email:42s} age={age_s:>6s} -> {st} http={http}")
                # 回写 accounts.jsonl(health_status + last_checked), 账号库保持活状态
                try:
                    update_account_health(cfg, email=email, health_status=st, http=http)
                except Exception as exc:
                    print(f"      [回写失败] {type(exc).__name__}: {str(exc)[:60]}")
            except Exception as exc:
                results.append((email, mtype, "error", None, age))
                print(f"  [{i}/{len(accounts)}] {mtype:9s} {email:42s} -> 异常 {type(exc).__name__}: {str(exc)[:40]}")
    finally:
        rot.close()

    _summarize(results)
    return 0


def _summarize(results: list[tuple[str, str, str, int | None, float]]) -> None:
    """汇总: 总数 + 按号源存活率 + 吊销/存活年龄分布。"""
    ok = sum(1 for _, _, s, _, _ in results if s == "ok")
    dead = sum(1 for _, _, s, _, _ in results if s in ("invalidated", "deactivated"))
    other = len(results) - ok - dead
    print(f"\n存活: {ok}/{len(results)}  吊销/封禁: {dead}  其他(error/限流): {other}")

    # 按号源存活率
    by_src: dict[str, list[str]] = defaultdict(list)
    for _, mt, s, _, _ in results:
        by_src[mt].append(s)
    print("\n按号源存活率:")
    for mt in sorted(by_src):
        ss = by_src[mt]
        o = sum(1 for s in ss if s == "ok")
        d = sum(1 for s in ss if s in ("invalidated", "deactivated"))
        rate = o / len(ss) * 100 if ss else 0
        print(f"  {mt:10s}: 存活 {o}/{len(ss)} ({rate:.0f}%)  吊销 {d}")

    # 吊销账号存活时长分布(注册后多久被吊销)
    dead_ages = sorted(round(a, 1) for _, _, s, _, a in results if s in ("invalidated", "deactivated"))
    if dead_ages:
        print(f"\n吊销账号存活时长分布({len(dead_ages)} 个):")
        buckets = Counter()
        for a in dead_ages:
            if a < 1: buckets["<1h"] += 1
            elif a < 3: buckets["1-3h"] += 1
            elif a < 6: buckets["3-6h"] += 1
            elif a < 24: buckets["6-24h"] += 1
            else: buckets["1d+"] += 1
        for k in ("<1h", "1-3h", "3-6h", "6-24h", "1d+"):
            if buckets[k]:
                print(f"  {k:6s}: {buckets[k]} 个")

    # 存活账号年龄分布(注册后已存活多久)——判断号源长期可靠性
    ok_ages = [round(a, 1) for _, _, s, _, a in results if s == "ok"]
    if ok_ages:
        print(f"\n存活账号年龄分布({len(ok_ages)} 个, 注册后已存活):")
        buckets = Counter()
        for a in ok_ages:
            if a < 1: buckets["<1h"] += 1
            elif a < 3: buckets["1-3h"] += 1
            elif a < 6: buckets["3-6h"] += 1
            elif a < 24: buckets["6-24h"] += 1
            else: buckets["1d+"] += 1
        for k in ("<1h", "1-3h", "3-6h", "6-24h", "1d+"):
            if buckets[k]:
                print(f"  {k:6s}: {buckets[k]} 个")

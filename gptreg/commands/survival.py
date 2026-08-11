"""survival: 批量测活 2FA 账号(定期换出口 IP 规避 403 风控)。

从 capture/tools/check_survival_batch.py 收编(带回 --source 过滤), 记录回写 health_status。
背景: 连续 accounts/check 同一 IP 会被 OpenAI WAF 拦成 403 HTML(blocked),
被误判为账号死亡(实际 IP 风控)。每 ROTATE_EVERY 个换一次 sid。
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any

from gptreg.account_store import load_accounts, mail_type_of, update_account_health
from gptreg.commands.common import RotatingSession, age_h, age_h_float, apply_region
from gptreg.health import check_account_health
from gptreg.session import BrowserSession


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("survival", help="批量测活 2FA 账号(定期换 IP, 回写 health)")
    p.add_argument("--limit", type=int, default=0, help="只测最近 N 个(0=全部)")
    p.add_argument("--rotate", type=int, default=8, help="每 N 个换一次出口 IP")
    p.add_argument("--email", default="", help="逗号分隔指定邮箱(覆盖默认全部)")
    p.add_argument("--source", default="", help="只测指定号源(ms_oauth/icloud/cloudmail), 与 --email 叠加")
    p.add_argument("--region", default=None, help="动态代理地区(覆盖 config), 如 TR/US/JP/NL")
    p.add_argument(
        "--proxy", default="",
        help="固定代理测活(推荐, 如 http://127.0.0.1:10808)。me 端点不触发同 IP 风控, "
             "固定代理比动态快 6 倍(实测 15 号: 16s vs 101s)。空=走动态代理换 IP(旧行为)。",
    )
    p.add_argument(
        "--workers", type=int, default=5,
        help="固定代理模式并发测活线程数(默认 5, 实测 15 号 16s→3s)。仅 --proxy 时生效; "
             "动态代理模式忽略(隧道单 socket 并发排队)。",
    )
    p.set_defaults(func=run)


def _load_2fa_accounts(cfg: dict[str, Any]) -> list[dict]:
    """accounts.jsonl 里带 totp_secret + access_token 的记录(按时间降序)。"""
    recs = [d for d in load_accounts(cfg) if d.get("totp_secret") and d.get("access_token")]
    recs.sort(key=lambda d: str(d.get("saved_at") or d.get("updated_at") or ""), reverse=True)
    return recs


def _promo_info(r: dict) -> tuple[str, bool]:
    """accounts/check 响应 → 优惠资格标记 (display_str, has_promo)。

    对齐 at-hub 的正确优惠字段(而非 promo_data):
      - eligible_promo_campaigns: 优惠活动(metadata.plan_name/title/discount)
      - eligible_offers: 可购买 offer 列表
      - is_eligible_for_yearly_plus_subscription: 年付 plus 资格
      - has_previously_paid_subscription: 历史付费
    空则只有 plan 标记(免费无订阅, subscriptions 接口返回 404)。
    """
    try:
        d = json.loads(r.get("body") or "")
        accs = d.get("accounts") or {}
        if not accs:
            return "", False
        a = next(iter(accs.values())).get("account") or {}
        flags: list[str] = []
        has = False
        # 优惠活动: {campaign_id: {id, metadata:{plan_name,title,discount}}}
        promo = a.get("eligible_promo_campaigns")
        if promo and isinstance(promo, dict):
            items = []
            for _k, v in promo.items():
                m = (v or {}).get("metadata") or {}
                name = m.get("plan_name") or m.get("title") or ""
                disc = m.get("discount")
                items.append(f"{name}={disc}" if disc else name)
            items = [x for x in items if x]
            if items:
                flags.append("promo=" + ",".join(items)[:60])
                has = True
        offers = a.get("eligible_offers")
        if offers:
            ids = [o.get("id") if isinstance(o, dict) else str(o) for o in (offers if isinstance(offers, list) else [])]
            ids = [x for x in ids if x][:3]
            if ids:
                flags.append(f"offers={ids}")
                has = True
        if a.get("is_eligible_for_yearly_plus_subscription"):
            flags.append("yearly_plus")
            has = True
        if a.get("has_previously_paid_subscription"):
            flags.append("paid")
            has = True
        flags.append(f"plan={(a.get('plan_type') or '?')}")
        return " ".join(flags), has
    except Exception:
        return "", False


def run(cfg: dict[str, Any], args) -> int:
    apply_region(cfg, args.region)
    accounts = _load_2fa_accounts(cfg)
    if args.source:
        accounts = [d for d in accounts if mail_type_of(d) == args.source]
    if args.email:
        emails = {e.strip() for e in args.email.split(",") if e.strip()}
        accounts = [d for d in accounts if d.get("email") in emails]
    if args.limit:
        accounts = accounts[: args.limit]
    fixed_proxy = (args.proxy or "").strip()
    t_start = time.time()
    # 固定代理 + 并发：me 端点同 IP 并发不触发 WAF，实测 workers=5 提速 ~5x
    # (15 号 16s→3s)。动态代理模式保持串行(隧道单 socket 并发会排队)。
    if fixed_proxy and int(getattr(args, "workers", 0) or 0) > 0:
        return _run_parallel_fixed(cfg, accounts, fixed_proxy, workers=int(args.workers), t_start=t_start)
    if fixed_proxy:
        # 固定代理测活(me 不触发同 IP 风控, 比动态快 ~6 倍): 每账号独立 BrowserSession, 共享固定代理
        print(f"测活 {len(accounts)} 个 2FA 账号(固定代理 {fixed_proxy}):")
    else:
        print(f"测活 {len(accounts)} 个 2FA 账号(每 {args.rotate} 个换出口 IP):")

    results: list[tuple[str, str, str, int | None, float]] = []  # (email, type, status, http, age_h)
    rot = RotatingSession(cfg, rotate=args.rotate)
    try:
        for i, d in enumerate(accounts, 1):
            if fixed_proxy:
                # 固定代理: 每账号独立 session(共享 proxy URL), me 同 IP 并发/串行都不风控
                sess = BrowserSession(cfg, proxy=fixed_proxy)
            else:
                sess = rot.get(i)  # 每 rotate 个重建(换出口 IP)
                if rot.rotated:
                    # 首个会话(首次建) vs 真正轮换(达到 rotate 间隔)——措辞区分, 不再误报轮换
                    if rot.is_first:
                        print(f"  [出口{i}] sid={rot.sid or '?'}")
                    else:
                        print(f"  [轮换@{i}] 新出口 sid={rot.sid or '?'}")

            email = d.get("email", "?")
            mtype = mail_type_of(d)
            age = age_h_float(d.get("saved_at") or d.get("updated_at") or "")
            age_s = age_h(age)
            # 固定代理路径: 每账号 session 需设 device_id + cookies(rot.get 已设, 固定路径没有)
            if fixed_proxy:
                sess.device_id = d.get("device_id") or ""
                for c in (d.get("session_cookies") or []):
                    try:
                        sess.session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain", ""))
                    except Exception:
                        pass
            _retried = False  # 每账号坏隧道重试标记
            try:
                _t0 = time.time()
                # 测活用短超时(10s)：隧道坏时(连接超时/出口不可达)快速失败, 不等满 session 60s
                r = check_account_health(sess, d.get("access_token"), timeout=10)
                dt = time.time() - _t0
                st = r.get("status")
                http = r.get("http")
                # 坏隧道(http=None, 连接类 error)换出口重试一次——动态代理随机 sid 有的出口不可达
                if st == "error" and http is None and not _retried:
                    _retried = True
                    try:
                        print(f"      [换 IP 重试] 隧道连接异常, force_rotate ...")
                        sess = rot.force_rotate()
                        r = check_account_health(sess, d.get("access_token"), timeout=10)
                        dt = time.time() - _t0
                        st = r.get("status")
                        http = r.get("http")
                    except Exception as exc:
                        print(f"      [重试异常] {type(exc).__name__}: {str(exc)[:50]}")
                promo_str, has_promo = _promo_info(r)
                results.append((email, mtype, st, http, age))
                if st == "error":
                    # error 分两类: 401=token 过期(可续期) vs 无 http=网络异常——显示真实 http
                    det = str(r.get("detail") or r.get("body") or "")[:70]
                    http_s = "None" if http is None else http
                    print(f"  [{i}/{len(accounts)}] {mtype:9s} {email:42s} age={age_s:>6s} -> error http={http_s} ({dt:.1f}s) [{det}]")
                elif promo_str:
                    # 优惠资格标记(测活顺带观察: promo/paid/gratis/plan); token_expired 也会显示
                    print(f"  [{i}/{len(accounts)}] {mtype:9s} {email:42s} age={age_s:>6s} -> {st} http={http} ({dt:.1f}s)  [{promo_str}]")
                else:
                    print(f"  [{i}/{len(accounts)}] {mtype:9s} {email:42s} age={age_s:>6s} -> {st} http={http} ({dt:.1f}s)")
                # 回写 accounts.jsonl(health_status + last_checked); 有优惠资格时记入 health_note,
                # error 时 detail 也落盘; token_expired 标注"可续期"(复盘/续期有据)
                try:
                    note = promo_str if has_promo else ""
                    if st == "error":
                        det_full = str(r.get("detail") or r.get("body") or "")[:200]
                        if det_full:
                            note = f"{note} | {det_full}" if note else det_full
                    elif st == "token_expired":
                        note = (f"{note} | " if note else "") + "access_token 过期, 可续期"
                    update_account_health(cfg, email=email, health_status=st, http=http, note=note)
                except Exception as exc:
                    print(f"      [回写失败] {type(exc).__name__}: {str(exc)[:60]}")
            except Exception as exc:
                results.append((email, mtype, "error", None, age))
                print(f"  [{i}/{len(accounts)}] {mtype:9s} {email:42s} -> 异常 {type(exc).__name__}: {str(exc)[:40]}")
            finally:
                if fixed_proxy:
                    try:
                        sess.close()
                    except Exception:
                        pass
    finally:
        rot.close()

    _summarize(results)
    tok_exp = sum(1 for _, _, s, _, _ in results if s == "token_expired")
    if tok_exp:
        print(f"提示: {tok_exp} 个 access_token 过期(非吊销, 有 session_token 可续期), 可运行 `main.py refresh` 续期")
    print(f"\n[总耗时] {(time.time()-t_start):.1f}s")
    return 0


def _run_parallel_fixed(cfg: dict[str, Any], accounts: list[dict], proxy: str, *, workers: int, t_start: float) -> int:
    """固定代理 + 并发测活：me 端点同 IP 并发不触发 WAF，实测 workers=5 提速 ~5x。

    vs 动态代理：固定代理连接复用(每账号独立 BrowserSession 共享 proxy URL)，
    无隧道建立开销；并发进一步加速。每个 job 独立 session + 独立 me 请求。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = len(accounts)
    print(f"测活 {n} 个 2FA 账号(固定代理 {proxy}, 并发 {workers}):")
    results: list[tuple[str, str, str, int | None, float]] = []

    def _one(d: dict) -> tuple:
        email = d.get("email", "?")
        mtype = mail_type_of(d)
        age = age_h_float(d.get("saved_at") or d.get("updated_at") or "")
        _t0 = time.time()
        try:
            sess = BrowserSession(cfg, proxy=proxy)
            sess.device_id = d.get("device_id") or ""
            for c in (d.get("session_cookies") or []):
                try:
                    sess.session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain", ""))
                except Exception:
                    pass
            r = check_account_health(sess, d.get("access_token"), timeout=10)
            dt = time.time() - _t0
            st = r.get("status")
            http = r.get("http")
            promo_str, has_promo = _promo_info(r)
            # 坏隧道(http=None, 连接类 error)同代理重试一次(网络抖动自愈)
            if st == "error" and http is None:
                try:
                    sess.close()
                except Exception:
                    pass
                sess = BrowserSession(cfg, proxy=proxy)
                sess.device_id = d.get("device_id") or ""
                for c in (d.get("session_cookies") or []):
                    try:
                        sess.session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain", ""))
                    except Exception:
                        pass
                r = check_account_health(sess, d.get("access_token"), timeout=10)
                dt = time.time() - _t0
                st = r.get("status")
                http = r.get("http")
                promo_str, has_promo = _promo_info(r)
            try:
                sess.close()
            except Exception:
                pass
            try:
                note = promo_str if has_promo else ""
                if st == "error":
                    det_full = str(r.get("detail") or r.get("body") or "")[:200]
                    if det_full:
                        note = f"{note} | {det_full}" if note else det_full
                elif st == "token_expired":
                    note = (f"{note} | " if note else "") + "access_token 过期, 可续期"
                update_account_health(cfg, email=email, health_status=st, http=http, note=note)
            except Exception as exc:
                print(f"      [回写失败] {type(exc).__name__}: {str(exc)[:60]}")
            return (email, mtype, st, http, age, promo_str, r, dt)
        except Exception as exc:
            return (email, mtype, "error", None, age, "", {}, 0.0)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_one, d): d for d in accounts}
        for fut in as_completed(futs):
            email, mtype, st, http, age, promo_str, r, dt = fut.result()
            results.append((email, mtype, st, http, age))
            done += 1
            age_s = age_h(age)
            if st == "error":
                det = str(r.get("detail") or r.get("body") or "")[:70]
                http_s = "None" if http is None else http
                print(f"  [{done}/{n}] {mtype:9s} {email:42s} age={age_s:>6s} -> error http={http_s} ({dt:.1f}s) [{det}]")
            elif promo_str:
                print(f"  [{done}/{n}] {mtype:9s} {email:42s} age={age_s:>6s} -> {st} http={http} ({dt:.1f}s)  [{promo_str}]")
            else:
                print(f"  [{done}/{n}] {mtype:9s} {email:42s} age={age_s:>6s} -> {st} http={http} ({dt:.1f}s)")

    _summarize(results)
    tok_exp = sum(1 for _, _, s, _, _ in results if s == "token_expired")
    if tok_exp:
        print(f"提示: {tok_exp} 个 access_token 过期(非吊销, 有 session_token 可续期), 可运行 `main.py refresh` 续期")
    print(f"\n[总耗时] {(time.time()-t_start):.1f}s")
    return 0


def _summarize(results: list[tuple[str, str, str, int | None, float]]) -> None:
    """汇总: 总数 + 按号源存活率 + 吊销/过期可续期/存活年龄分布。"""
    ok = sum(1 for _, _, s, _, _ in results if s == "ok")
    dead = sum(1 for _, _, s, _, _ in results if s in ("invalidated", "deactivated"))
    tok = sum(1 for _, _, s, _, _ in results if s == "token_expired")
    other = len(results) - ok - dead - tok
    print(f"\n存活: {ok}/{len(results)}  吊销/封禁: {dead}  过期可续期: {tok}  其他: {other}")

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

"""eligibility: 查询账号优惠资格/订阅状态(只读, 无副作用)。

1. accounts/check → account_id + eligible_promo_campaigns/eligible_offers(优惠活动)
2. promo_campaign/check_coupon → 优惠券资格(显式 eligible)
3. subscriptions?account_id → 订阅详情(free 无订阅返回 404)
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

from gptreg.account_store import load_accounts
from gptreg.commands.common import account_api_headers, apply_region, resolve_proxy_arg
from gptreg.proxyutil import resolve_proxy
from gptreg.session import BrowserSession

ACCOUNTS_CHECK = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
SUBSCRIPTIONS = "https://chatgpt.com/backend-api/subscriptions"
OFFER_CHECK_URL = "https://chatgpt.com/backend-api/promo_campaign/check_coupon"
OFFER_CAMPAIGN_ID = "plus-1-month-free"


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "eligibility",
        help="查询账号优惠资格/订阅状态(accounts/check + check_coupon + subscriptions)",
    )
    p.add_argument("--email", default="", help="逗号分隔指定邮箱(默认最近 N 个)")
    p.add_argument("--limit", type=int, default=5, help="查询数量(默认 5)")
    p.add_argument("--region", default=None, help="动态代理地区(覆盖 config)")
    p.add_argument("--proxy", default=None, help="覆盖代理；传 empty/none/direct 表示直连")
    p.add_argument("--no-proxy", action="store_true", help="强制直连")
    p.add_argument("--workers", type=int, default=8, help="并发探测线程数(默认 8, 探测池复用)")
    p.set_defaults(func=run)


def _query(sess: BrowserSession, account: dict[str, Any], at: str) -> dict[str, Any]:
    """三步查询: accounts/check → subscriptions → check_coupon。返回聚合 dict。"""
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = f"Bearer {at}"
    h["oai-device-id"] = sess.device_id
    h.pop("content-type", None)

    out: dict[str, Any] = {}
    a = sess.get(ACCOUNTS_CHECK, headers=h)
    out["accounts_http"] = a.status_code
    if a.status_code != 200:
        out["accounts_error"] = a.text[:150]
        return out
    accs = (a.json().get("accounts") or {})
    if not accs:
        out["accounts_error"] = "响应无 accounts 记录"
        return out
    rec = next(v for v in accs.values()) or {}
    out["account"] = rec.get("account") or {}
    # 资格字段在 accountRecord 层(rec), 非 account 层(修复层级 bug, at-hub 对齐)
    out["eligible_promo_campaigns"] = rec.get("eligible_promo_campaigns") or {}
    out["eligible_offers"] = rec.get("eligible_offers")
    out["yearly_plus"] = rec.get("is_eligible_for_yearly_plus_subscription")
    out["entitlement"] = rec.get("entitlement") or {}
    account_id = str(out["account"].get("account_id") or "").strip()
    out["account_id"] = account_id
    if not account_id:
        out["subs_http"] = "no_account_id"
        return out

    s = sess.get(f"{SUBSCRIPTIONS}?account_id={account_id}", headers=h)
    out["subs_http"] = s.status_code
    if s.status_code == 200:
        try:
            out["subscription"] = s.json()
        except Exception:
            out["subscription"] = {}
    elif s.status_code != 404:
        out["subs_error"] = s.text[:150]
    out["coupon_check"] = _check_coupon(sess, account, at, OFFER_CAMPAIGN_ID)
    return out


def _first_present(obj: Any, keys: tuple) -> Any:
    """递归找第一个非空字段(移植 register-kit first_present)。"""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return obj[k]
        for v in obj.values():
            got = _first_present(v, keys)
            if got not in (None, ""):
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _first_present(v, keys)
            if got not in (None, ""):
                return got
    return None


def _check_coupon(sess: BrowserSession, account: dict[str, Any], at: str, promo_id: str) -> dict[str, Any]:
    """GET promo_campaign/check_coupon 查优惠券资格(显式 eligible, register-kit 对齐头)。"""
    h = account_api_headers(sess, account, at, "/backend-api/promo_campaign/check_coupon")
    params = urlencode({"coupon": promo_id, "is_coupon_from_query_param": "false"})
    try:
        r = sess.get(f"{OFFER_CHECK_URL}?{params}", headers=h)
        status_code = r.status_code
        try:
            body = r.json()
        except Exception:
            body = {}
    except Exception as e:
        status_code = 0
        body = {"error": str(e)[:200]}

    state = str(_first_present(body, ("state", "status", "eligibility", "result")) or "").strip().lower()
    explicit = _first_present(body, ("eligible", "is_eligible"))
    request_failed = status_code in {0, 401, 403, 429}
    eligible_states = {"eligible", "valid", "available", "ok", "success"}
    ineligible_states = {"ineligible", "not_eligible", "redeemed", "already_redeemed",
                         "expired", "unavailable", "invalid"}
    if (state in eligible_states or explicit is True) and not request_failed:
        eligible, first_month, text = True, "yes", "优惠券可用"
    elif (state in ineligible_states or explicit is False) and not request_failed:
        eligible, first_month, text = False, "no", "优惠券不可用"
    else:
        eligible, first_month = None, "unknown"
        text = "请求失败，不能判断" if request_failed else "未检测 / 未确认"
    msg = _first_present(body, ("message", "error", "detail", "reason"))
    if msg:
        text += "：" + str(msg)[:240]
    return {
        "status_code": status_code,
        "eligible": eligible,
        "state": state,
        "first_month_offer": first_month,
        "request_failed": request_failed,
        "text": text,
    }


def _print_card(email: str, r: dict[str, Any]) -> None:
    acct = r.get("account") or {}
    sub = r.get("subscription") or {}
    print(f"  account_id: {r.get('account_id') or '?'}")
    # 优惠活动(accounts/check, accountRecord 层)
    promos = r.get("eligible_promo_campaigns")
    if promos and isinstance(promos, dict):
        for k, v in promos.items():
            m = (v or {}).get("metadata") or {}
            # plus 键 = Plus 试用资格(社区 openai-promo-bypass: 查 eligible_promo_campaigns.plus.id)
            tag = "  ⭐试用Plus" if k == "plus" else "  [优惠活动]"
            print(f"{tag} {m.get('plan_name') or k}  {m.get('title') or ''}  id={str((v or {}).get('id'))[:40]}  discount={m.get('discount')}")
    else:
        print("  [优惠活动] 无 (无 Plus 试用资格)")
    coupon = r.get("coupon_check")
    if coupon:
        if coupon.get("eligible") is True:
            print(f"  [优惠券] ✅可用 state={coupon.get('state') or '-'} {coupon.get('text','')}")
        elif coupon.get("eligible") is False:
            print(f"  [优惠券] ❌不可用 state={coupon.get('state') or '-'} {coupon.get('text','')}")
        else:
            print(f"  [优惠券] ?未确认 http={coupon.get('status_code')} {coupon.get('text','')}")
    offers = r.get("eligible_offers")
    if offers:
        print(f"  [可购offer] {json.dumps(offers, ensure_ascii=False)[:120]}")
    print(f"  [年付plus资格] {r.get('yearly_plus')}")
    print(f"  [历史付费] {acct.get('has_previously_paid_subscription')}")

    # 订阅详情(subscriptions 接口)
    if r.get("subs_http") == 404:
        print("  [订阅] 无 (subscriptions 404 = free 无订阅)")
    elif r.get("subs_http") == 200:
        plan = sub.get("plan_type") or sub.get("subscription_plan") or "?"
        print(f"  [订阅] plan={plan}  active={sub.get('has_active_subscription')}")
        exp = sub.get("expires_at") or sub.get("active_until")
        print(f"     expires={exp or '?'}  renews={sub.get('renews_at') or sub.get('next_invoice_at') or '?'}")
        print(f"     gratis={sub.get('is_gratis')}  delinquent={sub.get('is_delinquent')}  cancels={sub.get('cancels_at') or '?'}")
        discs = sub.get("applied_discounts")
        if discs:
            print(f"     [已用折扣] {json.dumps(discs, ensure_ascii=False)[:150]}")
        sub_offers = sub.get("eligible_offers")
        if sub_offers:
            print(f"     [可购offer] {json.dumps(sub_offers, ensure_ascii=False)[:150]}")
    else:
        print(f"  [订阅] 查询异常: http={r.get('subs_http')} {str(r.get('subs_error',''))[:80]}")


def run(cfg: dict[str, Any], args) -> int:
    apply_region(cfg, args.region)
    accounts = [d for d in load_accounts(cfg) if d.get("access_token")]
    if args.email:
        emails = {e.strip() for e in args.email.split(",") if e.strip()}
        accounts = [d for d in accounts if d.get("email") in emails]
    accounts = accounts[: args.limit]
    print(f"资格查询 {len(accounts)} 个账号:")

    fixed = resolve_proxy_arg(args)
    if fixed:
        # 显式 --proxy: 兼容旧路径(注意: 数据中心代理查不到 promo, 应传住宅代理)
        resolved = resolve_proxy(cfg, override=fixed)
        sess = BrowserSession(cfg, proxy=resolved.session_url)
        try:
            for i, d in enumerate(accounts, 1):
                print(f"\n[{i}/{len(accounts)}] {d.get('email')}")
                try:
                    _print_card(d.get("email"), _query(sess, d, d.get("access_token")))
                except Exception as exc:
                    print(f"  [查询异常] {type(exc).__name__}: {str(exc)[:80]}")
                time.sleep(0.5)
        finally:
            resolved.close()
    else:
        # 默认: 探测池(住宅隧道池, 独立于注册池)。region 取 config proxy.dynamic.trial_region(默认 JP),
        # 因 plus-1-month-free 是 JP 地区灰度活动, 只有 JP 出口查 eligible_promo_campaigns.plus 才非空。
        # 并发探测(workers): 探测池隧道复用, 每 worker 独立 session, 46 号 275s→~70s。
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from gptreg.proxyutil import ProxyPool

        _dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
        trial_region = str(_dyn.get("trial_region") or "JP")
        workers = max(1, int(getattr(args, "workers", 0) or 4))
        pool = ProxyPool(cfg, size=min(workers, max(1, len(accounts))), region=trial_region)

        def _one(d):
            # 请求失败(非 200/超时/连接异常)换隧道重试, 避免把"失败"误判成"无资格"(漏判根因)
            for _ in range(4):
                rp = pool.acquire()
                sess = BrowserSession(cfg, proxy=rp.session_url)
                try:
                    r = _query(sess, d, d.get("access_token"))
                    if r.get("accounts_http") == 200:
                        return d.get("email"), r
                except Exception:
                    pass
                finally:
                    sess.close()
                    pool.release(rp)
            return d.get("email"), None

        results: dict[str, Any] = {}
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_one, d): d for d in accounts}
                for fut in as_completed(futs):
                    email = futs[fut].get("email", "?")
                    try:
                        email, r = fut.result()
                    except Exception:
                        r = None
                    results[email] = r
            # 按 accounts 原顺序输出(并发完成顺序会乱, 这里按原顺序)
            for i, d in enumerate(accounts, 1):
                email = d.get("email")
                print(f"\n[{i}/{len(accounts)}] {email}")
                r = results.get(email)
                if r is None:
                    print(f"  [查询异常]")
                    continue
                try:
                    _print_card(email, r)
                except Exception as exc:
                    print(f"  [查询异常] {type(exc).__name__}: {str(exc)[:80]}")
        finally:
            pool.close()
    return 0

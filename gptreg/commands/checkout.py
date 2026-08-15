"""checkout: POST payments/checkout 下试用单 + OAICS 检测(服务端判定层)。

静态字段(accounts/check.eligible_promo_campaigns.plus)只是"发现层"; checkout 是否接受
promo 才是真正资格。返回的 checkout_session_id 按前缀分类(移植 register-kit):
  oaics_ = OAICS(首月优惠 cohort, 真资格) / cs_/cslive = 普通 Stripe Checkout。

⚠️ 有副作用(创建 checkout 草稿), 别大批量跑; 串行执行。
"""
from __future__ import annotations

import json
from typing import Any

from gptreg.account_store import load_accounts
from gptreg.commands.common import account_api_headers, apply_region, resolve_proxy_arg
from gptreg.proxyutil import resolve_proxy
from gptreg.session import BrowserSession

CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "checkout",
        help="下试用单+OAICS 检测(服务端判定层, 有副作用)",
    )
    p.add_argument("--email", default="", help="逗号分隔指定邮箱(默认最近 N 个)")
    p.add_argument("--limit", type=int, default=5, help="检测数量(默认 5)")
    p.add_argument("--region", default=None, help="动态代理地区(覆盖 config, 默认 trial_region=JP)")
    p.add_argument("--proxy", default=None, help="覆盖代理；传 empty/none/direct 表示直连")
    p.add_argument("--no-proxy", action="store_true", help="强制直连")
    p.add_argument("--promo", default="plus-1-month-free", help="promo campaign id")
    p.set_defaults(func=run)


def _classify_checkout_session(session_id: str) -> str:
    """按 checkout_session_id 前缀分类: oaics_=OAICS(首月优惠 cohort), cs_/cslive=普通 Stripe。"""
    sid = (session_id or "").strip().lower()
    if sid.startswith("oaics_"):
        return "OAICS"
    if sid.startswith("cs_") or sid.startswith("cslive") or sid.startswith("cs_live"):
        return "CS"
    return "UNKNOWN"


def _find_checkout_session_id(value: Any, depth: int = 0) -> str:
    """递归找 checkout_session_id(顶层取不到时下钻),移植 register-kit find_checkout_session_id。"""
    if depth > 12 or value is None:
        return ""
    if isinstance(value, dict):
        for key in ("checkout_session_id", "checkout_sessionid", "session_id", "id", "checkout_id"):
            v = value.get(key)
            if isinstance(v, str) and v.lower().startswith(("oaics_", "cs_", "cslive", "cs_live")):
                return v
        for v in value.values():
            found = _find_checkout_session_id(v, depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_checkout_session_id(item, depth + 1)
            if found:
                return found
    return ""


def _checkout(sess: BrowserSession, account: dict[str, Any], at: str, promo_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """POST payments/checkout 真正下试用单(服务端判定层, 有副作用)。"""
    from gptreg.rk_sentinel import ensure_sentinel_proxy, gen_sentinel_token
    ensure_sentinel_proxy(exit_proxy="socks5://127.0.0.1:10808")
    device_id = str(account.get("device_id") or "")
    b = cfg.get("browser") or {}
    token = gen_sentinel_token(
        device_id, "checkout_session_approval", b.get("user_agent") or "",
        page_url="https://chatgpt.com/checkout/openai_llc/cs_ctf",
        language=b.get("language") or "en-US", languages=b.get("languages") or "en-US,en;q=0.9",
        width=int(b.get("screen_width") or 1920), height=int(b.get("screen_height") or 1080),
        cores=int(b.get("hardware_concurrency") or 16), timezone=b.get("timezone") or "America/Los_Angeles",
    )
    h = account_api_headers(sess, account, at, "/backend-api/payments/checkout")
    h["openai-sentinel-token"] = token
    h["content-type"] = "application/json"
    body = {
        "plan_name": "chatgptplusplan",
        "entry_point": "all_plans_pricing_modal",
        "checkout_ui_mode": "hosted",
        "billing_details": {"country": "ID", "currency": "IDR"},
        "promo_campaign": {"promo_campaign_id": promo_id, "is_coupon_from_query_param": False},
    }
    r = sess.post(CHECKOUT_URL, headers=h, data=json.dumps(body))
    try:
        j = r.json()
    except Exception:
        j = {}
    session_id = str(j.get("checkout_session_id") or _find_checkout_session_id(j) or "")
    return {
        "http": r.status_code,
        "session_id": session_id,
        "checkout_type": _classify_checkout_session(session_id),
        "error": str(j.get("error") or ("" if r.status_code == 200 else (r.text or "")[:200])),
    }


def _print_checkout(r: dict[str, Any]) -> None:
    if r.get("session_id"):
        ctype = r.get("checkout_type") or "UNKNOWN"
        tag = "OAICS 真资格" if ctype == "OAICS" else ("CS 普通下单" if ctype == "CS" else "可下试用")
        print(f"  [checkout] ✅ {tag} session={r['session_id'][:40]}")
    else:
        print(f"  [checkout] ❌ 被拒 http={r.get('http')} {str(r.get('error'))[:100]}")


def run(cfg: dict[str, Any], args) -> int:
    apply_region(cfg, args.region)
    accounts = [d for d in load_accounts(cfg) if d.get("access_token")]
    if args.email:
        emails = {e.strip() for e in args.email.split(",") if e.strip()}
        accounts = [d for d in accounts if d.get("email") in emails]
    accounts = accounts[: args.limit]
    print(f"checkout 检测 {len(accounts)} 个账号(promo={args.promo}):")

    fixed = resolve_proxy_arg(args)
    if fixed:
        # 显式 --proxy: 数据中心代理下试用单会被拦, 应传住宅代理
        resolved = resolve_proxy(cfg, override=fixed)
        sess = BrowserSession(cfg, proxy=resolved.session_url)
        try:
            for i, d in enumerate(accounts, 1):
                print(f"\n[{i}/{len(accounts)}] {d.get('email')}")
                try:
                    _print_checkout(_checkout(sess, d, d.get("access_token"), args.promo, cfg))
                except Exception as exc:
                    print(f"  [异常] {type(exc).__name__}: {str(exc)[:80]}")
        finally:
            resolved.close()
    else:
        # 默认: 探测池(住宅隧道池), region 取 trial_region(默认 JP, plus-1-month-free 是 JP 灰度活动)
        from gptreg.proxyutil import ProxyPool

        _dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
        trial_region = str(_dyn.get("trial_region") or "JP")
        pool = ProxyPool(cfg, size=1, region=trial_region)
        try:
            for i, d in enumerate(accounts, 1):
                print(f"\n[{i}/{len(accounts)}] {d.get('email')}")
                rp = pool.acquire()
                sess = BrowserSession(cfg, proxy=rp.session_url)
                try:
                    _print_checkout(_checkout(sess, d, d.get("access_token"), args.promo, cfg))
                except Exception as exc:
                    print(f"  [异常] {type(exc).__name__}: {str(exc)[:80]}")
                finally:
                    sess.close()
                    pool.release(rp)
        finally:
            pool.close()
    return 0

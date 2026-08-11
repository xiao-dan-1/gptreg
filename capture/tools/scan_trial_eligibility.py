#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量扫描账号试用资格：遍历 accounts.jsonl 查 eligible_promo_campaigns.plus + entitlement.trial。

试用资格 = accounts/check 的 eligible_promo_campaigns 里有没有 plus 键(OpenAI 按活动分批发放)。
用法: python capture/tools/scan_trial_eligibility.py [--source ms_oauth] [--limit N] [--proxy URL]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.ERROR)

from gptreg.config import load_config
from gptreg.session import BrowserSession
from gptreg.proxyutil import resolve_proxy


def _scan_account(sess: BrowserSession, d: dict, region: str = "US") -> dict:
    """单账号查试用资格(两层, 只读不付款)。

    第一层: accounts/check 静态字段(eligible_promo_campaigns.plus / entitlement.trial)
    第二层: checkout 探测(JP 出口 + plus-1-month-free, 看 one_click_trial_eligible; 不付款)
    返回 {email, plan_type, trial, plus_promo, one_click, ok}。
    """
    out: dict = {"email": d.get("email", "?"), "ok": False}
    sess.device_id = d.get("device_id") or ""
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = "Bearer " + (d.get("access_token") or "")
    h["oai-device-id"] = sess.device_id

    # 第一层: 静态资格
    h2 = dict(h); h2.pop("content-type", None)
    resp = sess.get("https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27", headers=h2, timeout=10)
    out["http"] = resp.status_code
    if resp.status_code == 200:
        accs = resp.json().get("accounts") or {}
        acct = next(iter(accs.values())).get("account") or {} if accs else {}
        out["ok"] = True
        out["plan_type"] = acct.get("plan_type") or "?"
        ent = acct.get("entitlement") or {}
        out["trial"] = ent.get("trial")
        camp = acct.get("eligible_promo_campaigns") or {}
        out["plus_promo"] = bool(camp.get("plus"))
        if camp.get("plus"):
            out["plus_id"] = str(camp["plus"].get("id", ""))
        out["reactivation"] = acct.get("eligible_for_reactivation")
    return out


def _probe_checkout(sess: BrowserSession, token: str, region: str = "JP") -> dict:
    """第二层: checkout 探测(不付款)。JP 出口 + plus-1-month-free, 看资格信号。

    返回 {checkout_ok, one_click_trial_eligible, promo_applied}。
    注意: 会创建 checkout 草稿(不 approve/不付款), 用于探测资格而非真实账单。
    """
    out: dict = {"checkout_ok": False}
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = "Bearer " + token
    h["oai-device-id"] = sess.device_id
    h["content-type"] = "application/json"
    body = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": "JP", "currency": "JPY"},
        "checkout_ui_mode": "hosted",
        "promo_campaign": {"promo_campaign_id": "plus-1-month-free", "is_coupon_from_query_param": False},
    }
    try:
        resp = sess.post("https://chatgpt.com/backend-api/payments/checkout", headers=h, data=json.dumps(body), timeout=15)
        if resp.status_code != 200:
            out["checkout_http"] = resp.status_code
            return out
        d = resp.json()
        out["checkout_ok"] = True
        out["one_click_trial_eligible"] = d.get("one_click_trial_eligible")
        out["promo_applied"] = bool((d.get("promo_campaign") or {}).get("promo_campaign_id"))
        out["checkout_id"] = str(d.get("checkout_session_id", ""))[:24]
    except Exception as exc:
        out["checkout_err"] = str(exc)[:80]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="检测账号试用资格(只读, 不创建账单)")
    ap.add_argument("--limit", type=int, default=0, help="只扫最近 N 个(0=全部)")
    ap.add_argument("--proxy", default="", help="固定代理(如 http://127.0.0.1:10808)；空=动态")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--token", default="", help="直接传 access token 检测单账号(不依赖账号文件)")
    ap.add_argument("--region", default="US", help="accounts/check 用代理区域(默认 US)")
    ap.add_argument("--probe", action="store_true",
                    help="第二层: checkout 探测(JP 出口 + plus-1-month-free, 看 one_click_trial_eligible; 不付款)")
    args = ap.parse_args()

    cfg = load_config()
    from gptreg.account_store import load_accounts

    if args.token:
        # 单 token 检测(临时账号 dict)
        accounts = [{"email": "token-input", "access_token": args.token, "device_id": "probe-device"}]
    else:
        accounts = [d for d in load_accounts(cfg) if d.get("access_token")]
        if args.limit:
            accounts = accounts[: args.limit]
    print(f"扫描 {len(accounts)} 个账号的试用资格:")

    results = []
    proxy = args.proxy or None
    rp = resolve_proxy(cfg, override=proxy) if not proxy else None
    sess = BrowserSession(cfg, proxy=proxy or rp.session_url)
    try:
        for i, d in enumerate(accounts, 1):
            r = _scan_account(sess, d, region=args.region)
            if args.probe and r.get("ok"):
                r.update(_probe_checkout(sess, d.get("access_token"), region="JP"))
            results.append(r)
            plus = "⭐试用Plus!" if r.get("plus_promo") else ""
            trial = str(r.get("trial"))[:20] if r.get("trial") else "-"
            st = "ok" if r.get("ok") else f"http={r.get('http')}"
            one_click = ""
            if r.get("checkout_ok"):
                oc = "一键可试用!" if r.get("one_click_trial_eligible") else "无一键试用"
                one_click = f" | checkout:{oc}"
            print(f"  [{i}/{len(accounts)}] {str(r.get('email'))[:36]:38} plan={str(r.get('plan_type'))[:8]:8} trial={trial:18} {st} {plus}{one_click}")
            time.sleep(0.4)
    finally:
        sess.close()
        if rp:
            rp.close()

    n_plus = sum(1 for r in results if r.get("plus_promo"))
    n_trial = sum(1 for r in results if r.get("trial"))
    n_oc = sum(1 for r in results if r.get("one_click_trial_eligible"))
    print(f"\n=== 有试用Plus资格: {n_plus} | 有trial: {n_trial} | 一键可试用: {n_oc} ===")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

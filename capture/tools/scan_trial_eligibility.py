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

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

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
        rec = next(iter(accs.values())) if accs else {}  # accountRecord 层(accounts.<id>)
        acct = rec.get("account") or {}  # account 层(plan_type/eligible_for_reactivation 等)
        out["ok"] = True
        out["plan_type"] = acct.get("plan_type") or "?"
        ent = rec.get("entitlement") or {}  # 资格字段在 accountRecord 层, 非 account 层(修复层级 bug)
        out["trial"] = ent.get("trial")
        camp = rec.get("eligible_promo_campaigns") or {}  # 试用资格判据(at-hub 对齐)
        out["plus_promo"] = bool(camp.get("plus"))
        if camp.get("plus"):
            out["plus_id"] = str(camp["plus"].get("id", ""))
        out["reactivation"] = acct.get("eligible_for_reactivation")
        out["eligible_offers"] = rec.get("eligible_offers")
        out["yearly_plus"] = rec.get("is_eligible_for_yearly_plus_subscription")
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
        # 关键: promo 被接受 = 有资格(社区 openai-promo-bypass 判定)
        # promo_campaign 非空 = OpenAI 接受了 plus-1-month-free(可走完整 checkout 绑卡用)
        out["promo_accepted"] = bool((d.get("promo_campaign") or {}).get("promo_campaign_id"))
        out["checkout_id"] = str(d.get("checkout_session_id", ""))[:24]
    except Exception as exc:
        out["checkout_err"] = str(exc)[:80]
    return out


def _jp_probe(cfg: dict, token: str, device_id: str, max_sid: int = 6) -> dict:
    """JP 出口探测(多 sid 循环找日本 IP 再 checkout, 确保出口是 JP)。

    试用资格由出口 IP 决定: US 出口 promo 被拒, JP 出口才被接受。
    用 config 动态代理(region 强制 JP), 换 sid 直到确认出口 JP 再探测。
    返回 _probe_checkout 结果 + probe_exit (出口 country_code)。
    """
    from gptreg.proxyutil import build_dynamic_proxy, resolve_proxy

    _dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    trial_region = str(_dyn.get("trial_region") or "JP")
    for n in range(max_sid):
        jp_url = build_dynamic_proxy(cfg, region=trial_region)  # trial_region 出口
        resolved = resolve_proxy(cfg, override=jp_url)
        try:
            sess = BrowserSession(cfg, proxy=resolved.session_url)
            sess.device_id = device_id or "probe-device"
            # 确认出口地区
            try:
                r = sess.get("https://ipwho.is/", timeout=10)
                d = r.json()
                exit_cc = d.get("country_code", "?")
            except Exception:
                exit_cc = "?"
            # 非 trial_region 出口: 换 sid 重试
            if exit_cc != trial_region:
                sess.close()
                continue
            try:
                out = _probe_checkout(sess, token, region=trial_region)
                out["probe_exit"] = f"{exit_cc}:{str(d.get('ip'))[:15]}"
                return out
            finally:
                sess.close()
        finally:
            resolved.close()
    return {"checkout_ok": False, "probe_exit": "no_JP_exit"}


def main() -> int:
    ap = argparse.ArgumentParser(description="检测账号试用资格(只读, 不创建账单)")
    ap.add_argument("--limit", type=int, default=0, help="只扫最近 N 个(0=全部)")
    ap.add_argument("--proxy", default="", help="固定代理(如 http://127.0.0.1:10808)；空=动态")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--token", default="", help="直接传 access token 检测单账号(不依赖账号文件)")
    ap.add_argument("--region", default="US", help="accounts/check 用代理区域(默认 US)")
    ap.add_argument("--probe", action="store_true",
                    help="第二层: checkout 探测(JP 出口 + plus-1-month-free, 看 one_click_trial_eligible; 不付款)")
    ap.add_argument("--workers", type=int, default=1,
                    help="并发线程数(默认 1=串行；>1 时第一层并发筛选, JP probe 也并发, 大批量快)")
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
    workers = max(1, int(args.workers or 1))
    proxy = args.proxy or None
    # 统一用 config 的 trial_region(JP): 静态检查也走 JP 出口, 否则 eligible_promo_campaigns 空(US 无资格)
    _dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    trial_region = str(_dyn.get("trial_region") or "JP")
    if proxy:
        rp = None
        shared_proxy = proxy
    else:
        from gptreg.proxyutil import build_dynamic_proxy
        rp = resolve_proxy(cfg, override=build_dynamic_proxy(cfg, region=trial_region))
        shared_proxy = rp.session_url

    def _process_one(d: dict) -> dict:
        """单账号检测: 第一层(独立 session 共享代理) + 可选 JP probe。"""
        sess = BrowserSession(cfg, proxy=shared_proxy)
        try:
            r = _scan_account(sess, d, region=args.region)
        finally:
            sess.close()
        if args.probe and r.get("ok"):
            # 探测须走 JP 出口(试用资格由出口 IP 决定; US 出口 promo 被拒)。
            # _jp_probe 多 sid 循环直到出口确认 JP, 确保检测稳定。
            r.update(_jp_probe(cfg, d.get("access_token"), d.get("device_id") or ""))
        return r

    if workers > 1 and len(accounts) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_process_one, d): d for d in accounts}
            for fut in as_completed(futs):
                results.append(fut.result())
        results.sort(key=lambda r: str(r.get("email", "")))
    else:
        for d in accounts:
            results.append(_process_one(d))
    if rp:
        rp.close()

    for i, r in enumerate(results, 1):
        plus = "⭐试用Plus!" if r.get("plus_promo") else ""
        trial = str(r.get("trial"))[:20] if r.get("trial") else "-"
        st = "ok" if r.get("ok") else f"http={r.get('http')}"
        checkout_tag = ""
        if r.get("checkout_ok"):
            if r.get("promo_accepted"):
                checkout_tag = " | checkout:⭐促销被接受(有资格)"
            elif r.get("one_click_trial_eligible"):
                checkout_tag = " | checkout:⭐一键可试用!"
            else:
                checkout_tag = " | checkout:promo被拒"
        print(f"  [{i}/{len(results)}] {str(r.get('email'))[:34]:36} plan={str(r.get('plan_type'))[:8]:8} trial={trial:16} {st} {plus}{checkout_tag}")

    n_plus = sum(1 for r in results if r.get("plus_promo"))
    n_trial = sum(1 for r in results if r.get("trial"))
    n_oc = sum(1 for r in results if r.get("one_click_trial_eligible"))
    n_promo_acc = sum(1 for r in results if r.get("promo_accepted"))
    print(f"\n=== 静态Plus资格: {n_plus} | 有trial: {n_trial} | 一键可试用: {n_oc} | 促销被接受: {n_promo_acc} ===")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

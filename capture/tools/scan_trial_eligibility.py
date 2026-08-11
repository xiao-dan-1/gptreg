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


def _scan_account(sess: BrowserSession, d: dict) -> dict:
    """单账号查试用资格。返回 {email, plan_type, trial, plus_promo, ok}。"""
    out: dict = {"email": d.get("email", "?"), "ok": False}
    sess.device_id = d.get("device_id") or ""
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = "Bearer " + (d.get("access_token") or "")
    h["oai-device-id"] = sess.device_id
    h.pop("content-type", None)
    resp = sess.get("https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27", headers=h, timeout=10)
    out["http"] = resp.status_code
    if resp.status_code != 200:
        return out
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


def main() -> int:
    ap = argparse.ArgumentParser(description="批量扫描账号试用资格")
    ap.add_argument("--limit", type=int, default=0, help="只扫最近 N 个(0=全部)")
    ap.add_argument("--proxy", default="", help="固定代理(如 http://127.0.0.1:10808)；空=动态")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    cfg = load_config()
    from gptreg.account_store import load_accounts

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
            r = _scan_account(sess, d)
            results.append(r)
            plus = "⭐试用Plus!" if r.get("plus_promo") else ""
            trial = str(r.get("trial"))[:20] if r.get("trial") else "-"
            st = "ok" if r.get("ok") else f"http={r.get('http')}"
            print(f"  [{i}/{len(accounts)}] {str(r.get('email'))[:40]:42} plan={str(r.get('plan_type'))[:8]:8} trial={trial:20} {st} {plus}")
            time.sleep(0.4)
    finally:
        sess.close()
        if rp:
            rp.close()

    n_plus = sum(1 for r in results if r.get("plus_promo"))
    n_trial = sum(1 for r in results if r.get("trial"))
    print(f"\n=== 有试用Plus资格: {n_plus} | 有trial: {n_trial} ===")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

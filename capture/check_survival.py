"""存活抽查：重查 output/accounts.jsonl 里的账号 accounts/check。

用法:
    python capture/check_survival.py                      # 查所有带 token 的
    python capture/check_survival.py --emails a@x,b@x    # 只查指定邮箱
    python capture/check_survival.py --mode quickjs      # 只查某 sentinel 模式
"""
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gptreg import auth
from gptreg.config import load_config
from gptreg.session import BrowserSession
from gptreg.proxyutil import resolve_proxy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emails", default="", help="逗号分隔邮箱过滤")
    ap.add_argument("--mode", default="", help="按 sentinel 模式过滤: quickjs/browser/node/pow")
    ap.add_argument("--limit", type=int, default=0, help="最多检查 N 个（默认全部）")
    args = ap.parse_args()

    cfg = load_config()
    resolved = resolve_proxy(cfg)
    sess = BrowserSession(cfg, proxy=resolved.session_url)

    emails = {e.strip() for e in args.emails.split(",") if e.strip()}
    accounts = []
    for line in Path(ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not d.get("access_token"):
            continue
        if emails and d.get("email") not in emails:
            continue
        so = d.get("sentinel_obs") or {}
        if args.mode and so.get("challenge_mode") != args.mode:
            continue
        accounts.append(d)
    if args.limit:
        accounts = accounts[-args.limit:]

    print(f"检查 {len(accounts)} 个账号:")
    results = []
    for d in accounts:
        email = d.get("email")
        so = d.get("sentinel_obs") or {}
        mode = so.get("challenge_mode")
        t_len = so.get("t_len")
        orig_did = d.get("device_id")
        if orig_did:
            sess.device_id = orig_did
        try:
            r = auth.check_account_health(sess, d.get("access_token"))
            status = r.get("status")
            body = str(r.get("body") or r.get("detail") or "")[:60]
            line = f"  {email} [{mode}] t_len={t_len} -> {status} {body}"
            print(line)
            results.append((email, mode, status))
        except Exception as exc:
            line = f"  {email} [{mode}] -> 异常 {type(exc).__name__}: {str(exc)[:60]}"
            print(line)
            results.append((email, mode, "error"))
    resolved.close()

    ok = sum(1 for _, _, s in results if s == "ok")
    print(f"\n存活: {ok}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

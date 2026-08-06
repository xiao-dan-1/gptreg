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
    skipped_no_token = []
    for line in Path(ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if emails and d.get("email") not in emails:
            continue
        so = d.get("sentinel_obs") or {}
        if args.mode and so.get("challenge_mode") != args.mode:
            continue
        if not d.get("access_token"):
            skipped_no_token.append(d.get("email"))
            continue
        accounts.append(d)
    if args.limit:
        accounts = accounts[-args.limit:]

    if skipped_no_token:
        print(f"[提示] 跳过 {len(skipped_no_token)} 个无 access_token 的账号(需登录补 token 才能测活):")
        for e in skipped_no_token[:8]:
            print(f"    {e}")
        if len(skipped_no_token) > 8:
            print(f"    ... 共 {len(skipped_no_token)} 个")

    print(f"检查 {len(accounts)} 个账号:")
    results = []
    import time as _time
    t_start = _time.time()
    def _age_str(saved_at: str) -> str:
        """saved_at → 存活时长(中文可读)。"""
        try:
            from datetime import datetime
            t = datetime.fromisoformat(saved_at)
            h = (_time.time() - t.timestamp()) / 3600
            if h < 1:
                return f"{h*60:.0f}min"
            return f"{h:.1f}h"
        except Exception:
            return "?"

    for i, d in enumerate(accounts, 1):
        email = d.get("email")
        so = d.get("sentinel_obs") or {}
        mode = so.get("challenge_mode")
        t_len = so.get("t_len")
        so_len = so.get("create_so_len", so.get("so_len"))
        has_so = bool(so.get("create_has_so", so.get("has_so")))
        so_str = f"so=Y({so_len})" if has_so else "so=N"
        age = _age_str(d.get("saved_at") or "")
        orig_did = d.get("device_id")
        if orig_did:
            sess.device_id = orig_did
        t_one = _time.time()
        try:
            r = auth.check_account_health(sess, d.get("access_token"))
            status = r.get("status")
            body = str(r.get("body") or r.get("detail") or "")[:50]
            line = (f"  [{i}/{len(accounts)}] {email} [{mode}] "
                    f"t={t_len} {so_str} age={age} -> {status}")
            print(line)
            results.append((email, mode, status, age, has_so, t_len))
        except Exception as exc:
            status = "error"
            line = (f"  [{i}/{len(accounts)}] {email} [{mode}] "
                    f"t={t_len} {so_str} age={age} -> 异常 {type(exc).__name__}: {str(exc)[:40]}")
            print(line)
            results.append((email, mode, status, age, has_so, t_len))
        dt = _time.time() - t_one
        if dt > 2:
            print(f"      [耗时] 本账号 {dt:.1f}s")
    resolved.close()

    ok = sum(1 for r in results if r[2] == "ok")
    total = _time.time() - t_start
    print(f"\n存活: {ok}/{len(results)}  总耗时 {total:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

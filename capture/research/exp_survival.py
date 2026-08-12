#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""so 存活对照实验测活器: 对三组(VM-SO/BROWSER-SO/NO-SO)定时 me 检查。

用法: python capture/research/exp_survival.py [--interval 分钟] [--max-rounds N]
每组各指定账号前缀(实验 2026-08-12, Outlook + cliproxy 干净 IP)。
记录追加到 capture/research/exp-survival-20260812.md(兼容旧 tracking 格式)。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.account_store import load_accounts  # noqa: E402

GROUPS = {
    # 密码模式(quickjs_pwd_v3, register_pwd)新号 —— 预期长活(2026-08-12 根因定位后)
    "PWD-MODE": ["ScaceSchlarb69", "BralleyWrynn548", "KeliaBaptist23", "ThrelkeldBienvenu50"],
    # 对照: OTP-only create_account(已证 token 吊销) —— 标记死
    "OTP-ONLY(对照)": ["JordisonGustavson604", "FrentzelTigert02", "MacholParkhurst998"],
}
OUT = ROOT / "capture" / "research" / "exp-survival-20260812.md"

T0 = time.time()


def _check(sess: BrowserSession, at: str, device_id: str) -> tuple[str, int | None]:
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = f"Bearer {at}"
    h["oai-device-id"] = device_id
    h.pop("content-type", None)
    try:
        r = sess.get("https://chatgpt.com/backend-api/me", headers=h, timeout=12)
        body = (r.text or "")
        if r.status_code == 200:
            return "ok", 200
        if "account_deactivated" in body or "deactivated" in body:
            return "deactivated", r.status_code
        if "token_invalidated" in body:
            return "invalidated", r.status_code
        if "token_expired" in body:
            return "expired", r.status_code
        return f"http{r.status_code}", r.status_code
    except Exception:
        return "error", None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=10, help="测活间隔分钟(默认 10)")
    ap.add_argument("--max-rounds", type=int, default=0, help="轮数上限(0=无限)")
    ap.add_argument("--once", action="store_true", help="只测一轮(手动触发追加)")
    args = ap.parse_args()

    cfg = load_config()
    accounts = load_accounts(cfg)
    # 组 → [账号dict]
    grouped: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for g, prefixes in GROUPS.items():
        for p in prefixes:
            acc = next((d for d in accounts if p in d.get("email", "") and d.get("access_token")), None)
            if acc:
                grouped[g].append(acc)

    def _round(round_no: int) -> None:
        age_h = (time.time() - T0) / 3600
        lines = [f"### Round {round_no} (age {age_h:.2f}h, {time.strftime('%H:%M')})", ""]
        for g, accs in grouped.items():
            sess = BrowserSession(cfg, proxy="http://127.0.0.1:10808")
            for acc in accs:
                st, http = _check(sess, acc.get("access_token", ""), acc.get("device_id", ""))
                em = str(acc.get("email", "")).split("@")[0][:28]
                tag = "⭐" if st == "ok" else ""
                lines.append(f"| {g:10s} | {em:30s} | {st:12s} | http={http} | {tag}")
            sess.close()
        lines.append("")
        print("\n".join(lines))
        with OUT.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    print(f"实验账号: { {g: len(v) for g, v in grouped.items()} } → {OUT.name}")
    n = 0
    while True:
        n += 1
        _round(n)
        if args.once or (args.max_rounds and n >= args.max_rounds):
            break
        time.sleep(args.interval * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

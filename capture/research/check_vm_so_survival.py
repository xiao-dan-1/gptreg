#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时重测 vm-so 账号存活（so 纯程序获取验证的存活部分）。

用法:
    python capture/research/check_vm_so_survival.py [email 子串] [--sid SID]
输出一行状态, 供 cron/监控判断。
--sid 钉住动态代理 sid(同 IP 测活): 隔离"多 IP 轮换检查是否杀账号"变量。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import build_dynamic_proxy, resolve_proxy  # noqa: E402
from gptreg.health import check_account_health  # noqa: E402

_args = [a for a in sys.argv[1:] if not a.startswith("--")]
TARGET = _args[0] if _args else "cd0b35"
_SID = None
if "--sid" in sys.argv:
    _SID = sys.argv[sys.argv.index("--sid") + 1]


def main() -> int:
    cfg = load_config("config.yaml")
    acc = None
    p = Path("output/accounts.jsonl")
    if not p.exists():
        print(f"SURVIVAL_ERROR accounts.jsonl 不存在: {p.resolve()}")
        return 1
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if TARGET in d.get("email", ""):
            acc = d
            break
    if not acc:
        print(f"SURVIVAL_ERROR 未找到账号含 {TARGET}")
        return 1

    email = acc.get("email")
    saved_at = acc.get("saved_at") or ""
    try:
        age_min = round((datetime.now() - datetime.fromisoformat(saved_at)).total_seconds() / 60, 1)
    except Exception:
        age_min = "?"
    at = acc.get("access_token")
    if not at:
        print(f"SURVIVAL_ERROR {email} 无 access_token")
        return 1

    # --sid 传 URL 时直接用(注册同款钉死 URL); 传短 sid 时用 build_dynamic_proxy 生成
    _override = _SID if (_SID and "://" in _SID) else (build_dynamic_proxy(cfg, sid=_SID) if _SID else None)
    resolved = resolve_proxy(cfg, _override)
    sess = BrowserSession(cfg, proxy=resolved.session_url)
    try:
        r = check_account_health(sess, at)
        st = r.get("status")
        http = r.get("http")
        detail = str(r.get("detail") or r.get("body") or "")[:80]
        print(f"SURVIVAL {email} age={age_min}min sid={_SID or resolved.sid} -> {st} http={http} [{detail}]")
        return 0 if st == "ok" else 2
    finally:
        try:
            sess.close()
        except Exception:
            pass
        resolved.close()


if __name__ == "__main__":
    raise SystemExit(main())

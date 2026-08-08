#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性迁移落盘到统一 schema(accounts.jsonl 主库):
  - totp_accounts.txt 的 secret 补进 accounts.jsonl 匹配账号(totp_secret);
    不在 accounts.jsonl 的(probe 注册)追加为新行(含 secret, 无 at)
  - pwd_accounts.jsonl 并入 accounts.jsonl(status=ok)

运行前自动备份 accounts.jsonl 到 .bak-YYYYmmdd。

用法: python capture/migrate_persist.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ACC = ROOT / "output" / "accounts.jsonl"
TOTP = ROOT / "output" / "totp_accounts.txt"
PWD = ROOT / "data" / "pwd_accounts.jsonl"


def main() -> int:
    # 备份
    bak = ACC.with_suffix(f".jsonl.bak-{time.strftime('%Y%m%d%H%M%S')}")
    shutil.copy(ACC, bak)
    print(f"备份: {bak}")

    # 读现有 accounts
    recs = []
    for line in ACC.read_text(encoding="utf-8").splitlines():
        if line.strip():
            recs.append(json.loads(line))
    existing = {r.get("email"): r for r in recs}

    # totp_accounts.txt → 补 secret / 追加
    n_patch = n_new = 0
    if TOTP.exists():
        for line in TOTP.read_text(encoding="utf-8").splitlines():
            if "----" not in line:
                continue
            e, p, s = line.strip().split("----")
            if e in existing:
                existing[e]["totp_secret"] = s
                n_patch += 1
            else:
                existing[e] = {
                    "email": e, "password": p, "totp_secret": s,
                    "status": "ok", "updated_at": "",
                    "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                recs.append(existing[e])
                n_new += 1

    # pwd_accounts.jsonl → 并入
    n_pwd = 0
    if PWD.exists():
        for line in PWD.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("email") in existing:
                continue
            d.setdefault("status", "ok")
            d.setdefault("updated_at", d.get("saved_at") or "")
            recs.append(d)
            existing[d["email"]] = d
            n_pwd += 1

    # 写回
    with ACC.open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_secret = sum(1 for r in recs if r.get("totp_secret"))
    print(f"迁移完成: 总 {len(recs)} 条 | 补 secret {n_patch} | 追加 totp {n_new} | 并入 pwd {n_pwd} | 含 secret {n_secret}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

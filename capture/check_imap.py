#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查号池主号的 IMAP 可用性(决定收码走 IMAP 秒级还是降级 Graph 慢速)。

IMAP 不可用(缺 OAuth scope / 未开 IMAP) → build_mail_client 降级 Graph(150s+ 索引延迟)。
本脚本对号池主号逐个测 IMAP 连接, 统计可用比例。

用法: python capture/check_imap.py [--limit N]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402


def main() -> int:
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    accounts = []
    for line in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
        a = parse_mail_line(line.strip())
        if a:
            accounts.append(a)
    print(f"号池账号: {len(accounts)}")

    ok, fail, err = 0, 0, 0
    for i, a in enumerate(accounts[: args.limit], 1):
        try:
            client = build_mail_client(a)
            cls = type(client).__name__
            if cls == "IMAPOAuthClient":
                t0 = time.time()
                try:
                    conn = client.connect()
                    conn.select("INBOX", readonly=True)
                    client.close()
                    print(f"  [{i}/{args.limit}] {a['email']:40s} {cls}  ✅ IMAP 可用 ({(time.time()-t0):.1f}s)")
                    ok += 1
                except Exception as exc:
                    print(f"  [{i}/{args.limit}] {a['email']:40s} {cls}  ❌ IMAP 失败: {str(exc)[:60]} → 降级 Graph")
                    fail += 1
            else:
                print(f"  [{i}/{args.limit}] {a['email']:40s} {cls}  (非 IMAP 通道)")
                err += 1
        except Exception as exc:
            print(f"  [{i}/{args.limit}] {a['email']:40s} 构建失败: {str(exc)[:60]}")
            err += 1

    print(f"\nIMAP 可用 {ok} / 失败降级 {fail} / 其他 {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

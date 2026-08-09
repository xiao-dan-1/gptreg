"""imap: 检查号池主号 IMAP 可用性(决定收码走 IMAP 秒级还是降级 Graph 慢速)。

从 capture/tools/check_imap.py 收编, 修复 cwd 相对路径(Path("mail_pool.txt") →
resolve_path 根目录定位)。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from gptreg.config import resolve_path
from gptreg.mail.pool import parse_mail_line
from gptreg.mail.providers import build_mail_client


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("imap", help="检查号池主号 IMAP 可用性")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--pool", default=None, help="号池路径，覆盖配置")
    p.set_defaults(func=run)


def run(cfg: dict[str, Any], args) -> int:
    pool_file = resolve_path(
        args.pool or cfg.get("mail", {}).get("pool_file", "mail_pool.txt"),
        Path(cfg["_root"]),
    )
    accounts = []
    for line in pool_file.read_text(encoding="utf-8").splitlines():
        a = parse_mail_line(line.strip())
        if a:
            accounts.append(a)
    print(f"号池账号: {len(accounts)}")

    ok, fail, err = 0, 0, 0
    for i, a in enumerate(accounts[: args.limit], 1):
        try:
            client = build_mail_client(a, cfg=cfg)
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

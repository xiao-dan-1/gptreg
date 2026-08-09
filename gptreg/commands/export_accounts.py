"""export: 导出账号为可交付格式(---- 分隔, 与号池格式一致)。

从 capture/tools/export_accounts.py 收编。
格式: 默认 email----password----2fa; --with-at 加第4段 access_token。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from gptreg.account_store import load_accounts


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("export", help="导出账号 email----password----2fa[----at]")
    p.add_argument("--with-at", action="store_true", help="加第4段 access_token")
    p.add_argument("--filter", default="", choices=["", "alive", "dead"],
                   help="存活过滤: alive=health ok, dead=invalidated")
    p.add_argument("--source", default="", help="只导某号源(mail_type/域名)")
    p.add_argument("--out", default="", help="输出文件(默认打印)")
    p.set_defaults(func=run)


def run(cfg: dict[str, Any], args) -> int:
    recs = load_accounts(cfg)
    # 完整账号: email+password+totp(缺任一段不可交付)
    complete = [r for r in recs if r.get("email") and r.get("password") and r.get("totp_secret")]
    if args.filter == "alive":
        complete = [r for r in complete if r.get("health_status") == "ok"]
    elif args.filter == "dead":
        complete = [r for r in complete if r.get("health_status") in ("invalidated", "deactivated")]
    if args.source:
        src = args.source.lower()
        complete = [r for r in complete
                    if src in str(r.get("mail_type") or "").lower()
                    or src in str(r.get("email") or "").lower()]

    lines = []
    for r in complete:
        email, pw, totp = r["email"], r["password"], r["totp_secret"]
        if args.with_at:
            at = r.get("access_token") or ""
            if not at:
                continue  # 无 at 的账号不满足 4 段格式
            lines.append(f"{email}----{pw}----{totp}----{at}")
        else:
            lines.append(f"{email}----{pw}----{totp}")

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已导出 {len(lines)} 个账号 → {args.out}")
    else:
        for line in lines:
            print(line)
        print(f"\n(共 {len(lines)} 个)", file=sys.stderr)
    return 0

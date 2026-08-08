#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导出账号为可交付格式(---- 分隔, 与号池格式一致)。

格式:
  默认      email----password----2fa
  --with-at email----password----2fa----at        (at = access_token)

扩展: 将来需要其他格式时加 --format 参数或新增列, 本脚本结构支持。

用法:
    python capture/tools/export_accounts.py                    # 全部完整账号
    python capture/tools/export_accounts.py --with-at         # 带 access_token
    python capture/tools/export_accounts.py --filter alive    # 只存活
    python capture/tools/export_accounts.py --out out.txt     # 写文件
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_accounts() -> list[dict]:
    """主库 accounts.jsonl 全部记录。"""
    p = ROOT / "output" / "accounts.jsonl"
    recs = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    return recs


def main() -> int:
    ap = argparse.ArgumentParser(description="导出账号为 email----password----2fa[----at]")
    ap.add_argument("--with-at", action="store_true", help="加第4段 access_token")
    ap.add_argument("--filter", default="", choices=["", "alive", "dead"],
                    help="存活过滤: alive=health ok, dead=invalidated")
    ap.add_argument("--source", default="", help="只导某号源(mail_type/域名)")
    ap.add_argument("--out", default="", help="输出文件(默认打印)")
    args = ap.parse_args()

    recs = load_accounts()
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


if __name__ == "__main__":
    raise SystemExit(main())

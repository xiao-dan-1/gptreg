#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""筛选未注册邮箱:signin 后 follow authorize,看落点是密码页还是 OTP 页。

关键(学自 codex-register V3):未注册邮箱的 authorize 302 → /create-account/password;
已注册邮箱 → /email-verification(登录流程)。不耗 OTP,可批量。

用法:
    python capture/find_unregistered_email.py               # 全号池
    python capture/find_unregistered_email.py --limit 40
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from capture.verify_password_register import _base  # noqa: E402


def check_email(cfg, proxy_url: str, email: str) -> tuple[str, str]:
    """返回 (落点分类, 详情)。"""
    session = BrowserSession(cfg, proxy=proxy_url)
    try:
        auth.get_providers(session)
        time.sleep(0.2)
        csrf = auth.get_csrf_token(session)
        time.sleep(0.2)
        authorize_url = auth.signin_openai(session, csrf, email)
        time.sleep(0.2)
        final = auth.follow_authorize(session, authorize_url, attempts=1)
        if "create-account/password" in final or "password" in final:
            return "UNREGISTERED(pwd)", final[:70]
        if "email-verification" in final or "email_verification" in final:
            return "registered(otp)", final[:70]
        if "about-you" in final or "about_you" in final:
            return "about_you", final[:70]
        return "other", final[:70]
    except Exception as exc:
        return "error", f"{type(exc).__name__}: {str(exc)[:50]}"
    finally:
        try:
            session.session.close()
        except Exception:
            pass


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--save", default="data/unregistered_emails.txt")
    args = ap.parse_args()

    cfg = load_config()
    resolved = resolve_proxy(cfg, override=args.proxy or None)
    print(f"代理: {resolved.label()}")

    taken: set[str] = set()
    st_path = ROOT / "mail_pool.txt.state.json"
    if st_path.exists():
        try:
            for u in json.loads(st_path.read_text(encoding="utf-8")).get("used") or []:
                if isinstance(u, str):
                    taken.add(_base(u).lower())
        except Exception:
            pass
    acc_path = ROOT / "output" / "accounts.jsonl"
    if acc_path.exists():
        for line in acc_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("email"):
                    taken.add(_base(d["email"]).lower())
            except Exception:
                pass

    pool_file = str((cfg.get("mail") or {}).get("pool_file") or "mail_pool.txt")
    cands = []
    for line in Path(pool_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        a = parse_mail_line(line)
        if not a:
            continue
        if _base(a["email"]).lower() in taken:
            continue
        cands.append(a)
    if args.limit:
        cands = cands[: args.limit]
    print(f"候选 {len(cands)} 个")

    results = []
    for i, a in enumerate(cands):
        email = a["email"]
        kind, detail = check_email(cfg, resolved.session_url, email)
        flag = "✅" if kind == "UNREGISTERED(pwd)" else "  "
        print(f"{flag} [{i+1}/{len(cands)}] {email:<42} {kind} {detail}")
        results.append((email, kind, detail))

    unreg = [e for e, k, _ in results if k == "UNREGISTERED(pwd)"]
    print(f"\n=== 未注册(密码页)邮箱: {len(unreg)} 个 ===")
    for e in unreg:
        print(f"  {e}")
    if args.save and unreg:
        out = ROOT / args.save
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(unreg) + "\n", encoding="utf-8")
        print(f"已存 {out}")
    resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

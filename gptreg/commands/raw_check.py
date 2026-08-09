"""raw-check: 直接喂 access token(JWT) 测活(不经 accounts.jsonl)。

从 capture/tools/check_raw_tokens.py 收编, decode_jwt 下沉 gptreg/jwtutil。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from gptreg.commands.common import resolve_proxy_arg
from gptreg.health import check_account_health
from gptreg.jwtutil import decode_jwt
from gptreg.proxyutil import resolve_proxy
from gptreg.session import BrowserSession


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("raw-check", help="直接喂 access token 测活(不经 accounts.jsonl)")
    p.add_argument("--file", default="", help="token 文件(每行一个); 空则读 stdin")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--proxy", default=None, help="覆盖代理；传 empty/none/direct 表示直连")
    p.add_argument("--no-proxy", action="store_true", help="强制直连")
    p.set_defaults(func=run)


def run(cfg: dict[str, Any], args) -> int:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    tokens = [t.strip() for t in text.splitlines() if t.strip()]
    if args.limit:
        tokens = tokens[: args.limit]
    print(f"token 数: {len(tokens)}")

    resolved = resolve_proxy(cfg, override=resolve_proxy_arg(args))
    sess = BrowserSession(cfg, proxy=resolved.session_url)
    t_start = time.time()
    results = []
    try:
        for i, t in enumerate(tokens, 1):
            try:
                info = decode_jwt(t)
            except Exception as exc:
                print(f"  [{i}/{len(tokens)}] 解析失败: {type(exc).__name__}: {str(exc)[:40]}")
                results.append(("?", "parse_error"))
                continue
            email = info["email"]
            exp_ok = "Y" if info["exp"] > time.time() else "N"
            t_one = time.time()
            try:
                r = check_account_health(sess, t)
                status = r.get("status")
                body = str(r.get("body") or r.get("detail") or "")[:50]
                print(f"  [{i}/{len(tokens)}] {email:34s} {info['name'][:16]:16s} exp={exp_ok} -> {status} {body}")
                results.append((email, status))
            except Exception as exc:
                status = "error"
                print(f"  [{i}/{len(tokens)}] {email:34s} -> 异常 {type(exc).__name__}: {str(exc)[:40]}")
                results.append((email, status))
            dt = time.time() - t_one
            if dt > 3:
                print(f"      [耗时] 本账号 {dt:.1f}s")
    finally:
        resolved.close()

    ok = sum(1 for _, s in results if s == "ok")
    print(f"\n存活: {ok}/{len(results)}  总耗时 {time.time()-t_start:.1f}s")
    return 0

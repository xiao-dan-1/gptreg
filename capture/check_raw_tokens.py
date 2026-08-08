#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""直接喂 access token(JWT) 测活(不经 accounts.jsonl)。

用法:
    python capture/check_raw_tokens.py --file tokens.txt
    cat tokens.txt | python capture/check_raw_tokens.py        # stdin, 每行一个 JWT

逐个解码 payload(email/name/exp), 用 check_account_health 测活。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config
from gptreg.health import check_account_health
from gptreg.session import BrowserSession
from gptreg.proxyutil import resolve_proxy


def decode_jwt(token: str) -> dict:
    """解析 JWT payload(不验签), 返回 email/name/exp 或抛异常。"""
    token = re.sub(r"\s+", "", token)  # 粘贴可能被换行截断, 去掉所有空白
    seg = token.split(".")[1]
    seg += "=" * (-len(seg) % 4)
    p = json.loads(base64.urlsafe_b64decode(seg))
    prof = p.get("https://api.openai.com/profile", {}) or {}
    au = p.get("https://api.openai.com/auth", {}) or {}
    return {
        "email": prof.get("email", ""),
        "name": prof.get("name", ""),
        "exp": p.get("exp", 0),
        "acct": au.get("chatgpt_account_id", ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="", help="token 文件(每行一个); 空则读 stdin")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    tokens = [t.strip() for t in text.splitlines() if t.strip()]
    if args.limit:
        tokens = tokens[: args.limit]
    print(f"token 数: {len(tokens)}")

    cfg = load_config()
    resolved = resolve_proxy(cfg)
    sess = BrowserSession(cfg, proxy=resolved.session_url)
    t_start = time.time()
    results = []
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
    resolved.close()

    ok = sum(1 for _, s in results if s == "ok")
    print(f"\n存活: {ok}/{len(results)}  总耗时 {time.time()-t_start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

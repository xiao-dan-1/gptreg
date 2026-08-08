#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""access_token 续期: 用 session_cookies / sessionToken 重抓 /api/auth/session 换新 token。

背景(研究实证, 见 refresh-research-20260808.md):
  - access_token(JWT) 10 天过期; refresh_token 一般没有(OAuth 无此字段)
  - 刷新靠 session_cookies(35个) 或 sessionToken(JWE ~3月) → GET /api/auth/session
  - 返回新 access_token + sessionToken + expires(~3月)

用法:
    python capture/refresh_at.py                # 全部带 token 的账号续期
    python capture/refresh_at.py --email a@x   # 只续指定账号
    python capture/refresh_at.py --limit 5     # 只续最近 5 个
    python capture/refresh_at.py --dry-run     # 只探测(重抓但不回写), 看能否续
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config
from gptreg.session import BrowserSession
from gptreg.account_store import update_account_tokens
from gptreg.proxyutil import build_dynamic_proxy, random_sid, resolve_proxy, set_sid

SESSION_URL = "https://chatgpt.com/api/auth/session"


def _load_accounts() -> list[dict]:
    recs = []
    p = ROOT / "output" / "accounts.jsonl"
    if not p.exists():
        return recs
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            pass
    return recs


def _cookie_jar(sess: BrowserSession, cookies: list[dict]) -> None:
    for c in cookies or []:
        try:
            sess.session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain"))
        except Exception:
            pass


def _jar_to_list(sess: BrowserSession) -> list[dict]:
    return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
             "secure": bool(getattr(c, "secure", False))}
            for c in sess.session.cookies.jar]


def _fetch_new_session(sess: BrowserSession) -> dict:
    """用 cookies 重抓 session, 返回 dict(可能含 accessToken/sessionToken/expires)。"""
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h.pop("content-type", None)
    r = sess.get(SESSION_URL, headers=h, timeout=30)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {(r.text or '')[:120]}"}
    return r.json()


def _exp_days(access_token: str) -> str:
    """access_token JWT exp → 剩余天数。"""
    try:
        import base64
        seg = access_token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        pl = json.loads(base64.urlsafe_b64decode(seg))
        return f"{(pl.get('exp', 0) - time.time())/86400:.1f}d"
    except Exception:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="", help="逗号分隔指定邮箱")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="只探测(重抓 session 但不回写)")
    ap.add_argument("--rotate", type=int, default=5, help="每 N 个换一次出口 IP")
    args = ap.parse_args()

    accounts = _load_accounts()
    accounts = [d for d in accounts if d.get("access_token")]
    if args.email:
        emails = {e.strip() for e in args.email.split(",") if e.strip()}
        accounts = [d for d in accounts if d.get("email") in emails]
    if args.limit:
        accounts = accounts[: args.limit]
    print(f"续期 {len(accounts)} 个账号{' (DRY-RUN 不回写)' if args.dry_run else ''}:")

    cfg = load_config()
    resolved = None
    sess = None
    results = []
    for i, d in enumerate(accounts, 1):
        if resolved is None or (i - 1) % args.rotate == 0:
            if resolved is not None:
                resolved.close()
            new_url = set_sid(build_dynamic_proxy(cfg), sid=random_sid(8), sid_len=8)
            resolved = resolve_proxy(cfg, override=new_url)
            sess = BrowserSession(cfg, proxy=resolved.session_url)
            print(f"  [轮换{i}] 新出口 sid={resolved.sid or '?'}")

        email = d.get("email", "?")
        cookies = d.get("session_cookies") or []
        if not cookies:
            print(f"  [{i}/{len(accounts)}] {email:42s} 无 session_cookies, 跳过")
            continue
        _cookie_jar(sess, cookies)
        new = _fetch_new_session(sess)
        at = new.get("accessToken") or ""
        st = new.get("sessionToken") or ""
        expires = new.get("expires") or ""
        if "error" in new or not at:
            print(f"  [{i}/{len(accounts)}] {email:42s} 续期失败: {new.get('error', '无 accessToken')[:60]}")
            results.append((email, False, new.get("error", "")))
            continue
        old = d.get("access_token") or ""
        renewed = "是" if at != old else "否(同token)"
        print(f"  [{i}/{len(accounts)}] {email:42s} 续期成功 新token={_exp_days(at)} st={'Y' if st else 'N'} expires={expires[:10] or '?'} 续期={renewed}")
        if not args.dry_run:
            try:
                new_cookies = _jar_to_list(sess)
                update_account_tokens(cfg, email=email, access_token=at,
                                      session_token=st, session_cookies=new_cookies,
                                      expires=expires, health_status="ok")
            except Exception as exc:
                print(f"      [回写失败] {type(exc).__name__}: {str(exc)[:60]}")
        results.append((email, True, ""))
        time.sleep(0.5)

    if resolved is not None:
        resolved.close()
    ok = sum(1 for _, s, _ in results if s)
    print(f"\n续期成功: {ok}/{len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

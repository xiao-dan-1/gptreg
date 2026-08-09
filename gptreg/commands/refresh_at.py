"""refresh: access_token 续期(用 session_cookies/sessionToken 重抓 /api/auth/session)。

从 capture/tools/refresh_at.py 收编, cookie 注入/导出用 session 模块 helper。
背景: access_token(JWT) 10 天过期; refresh_token 一般没有(OAuth 无此字段);
刷新靠 session_cookies 或 sessionToken(JWE ~3月) → GET /api/auth/session。
"""
from __future__ import annotations

import time
from typing import Any

from gptreg.account_store import load_accounts, update_account_tokens
from gptreg.commands.common import RotatingSession
from gptreg.jwtutil import exp_days
from gptreg.session import jar_to_list, set_cookies

SESSION_URL = "https://chatgpt.com/api/auth/session"


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("refresh", help="access_token 续期(过期前跑, 账号永活)")
    p.add_argument("--email", default="", help="逗号分隔指定邮箱")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true", help="只探测(重抓 session 但不回写)")
    p.add_argument("--rotate", type=int, default=5, help="每 N 个换一次出口 IP")
    p.set_defaults(func=run)


def _fetch_new_session(sess, cookies: list[dict]) -> dict:
    """用 cookies 重抓 session, 返回 dict(可能含 accessToken/sessionToken/expires)。"""
    set_cookies(sess, cookies)
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h.pop("content-type", None)
    r = sess.get(SESSION_URL, headers=h, timeout=30)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {(r.text or '')[:120]}"}
    return r.json()


def run(cfg: dict[str, Any], args) -> int:
    accounts = [d for d in load_accounts(cfg) if d.get("access_token")]
    if args.email:
        emails = {e.strip() for e in args.email.split(",") if e.strip()}
        accounts = [d for d in accounts if d.get("email") in emails]
    if args.limit:
        accounts = accounts[: args.limit]
    print(f"续期 {len(accounts)} 个账号{' (DRY-RUN 不回写)' if args.dry_run else ''}:")

    rot = RotatingSession(cfg, rotate=args.rotate)
    results = []
    try:
        for i, d in enumerate(accounts, 1):
            sess = rot.get(i)  # 每 rotate 个重建(换出口 IP)
            if rot.rotated:
                print(f"  [轮换{i}] 新出口 sid={rot.sid or '?'}")

            email = d.get("email", "?")
            cookies = d.get("session_cookies") or []
            if not cookies:
                print(f"  [{i}/{len(accounts)}] {email:42s} 无 session_cookies, 跳过")
                continue
            new = _fetch_new_session(sess, cookies)
            at = new.get("accessToken") or ""
            st = new.get("sessionToken") or ""
            expires = new.get("expires") or ""
            if "error" in new or not at:
                print(f"  [{i}/{len(accounts)}] {email:42s} 续期失败: {new.get('error', '无 accessToken')[:60]}")
                results.append((email, False, new.get("error", "")))
                continue
            old = d.get("access_token") or ""
            renewed = "是" if at != old else "否(同token)"
            print(f"  [{i}/{len(accounts)}] {email:42s} 续期成功 新token={exp_days(at)} st={'Y' if st else 'N'} expires={expires[:10] or '?'} 续期={renewed}")
            if not args.dry_run:
                try:
                    update_account_tokens(cfg, email=email, access_token=at,
                                          session_token=st, session_cookies=jar_to_list(sess),
                                          expires=expires, health_status="ok")
                except Exception as exc:
                    print(f"      [回写失败] {type(exc).__name__}: {str(exc)[:60]}")
            results.append((email, True, ""))
            time.sleep(0.5)
    finally:
        rot.close()

    ok = sum(1 for _, s, _ in results if s)
    print(f"\n续期成功: {ok}/{len(results)}")
    return 0 if ok == len(results) else 1

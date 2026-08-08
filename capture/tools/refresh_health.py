#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""刷新 + 测活:用 session_cookies 刷新 access_token 后再 accounts/check,区分真死 vs token 过期。

旧账号(2026-08-05 前注册)无 session_cookies,但近期账号 access_token 仍有效(exp 未到),
可直接用 access_token 测。过期且无 cookies 的标记 unknown(无法判定)。

用法:
    python capture/refresh_health.py                # 全量
    python capture/refresh_health.py --mode browser # 只测某模式
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg import auth  # noqa: E402
from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402


def jwt_exp(token: str) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    try:
        parts = token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        iat = datetime.datetime.fromtimestamp(payload["iat"]) if payload.get("iat") else None
        exp = datetime.datetime.fromtimestamp(payload["exp"]) if payload.get("exp") else None
        return iat, exp
    except Exception:
        return None, None


def inject_cookies(sess: BrowserSession, cookies: list[dict]) -> None:
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        name, value = c.get("name"), c.get("value")
        if not name:
            continue
        domain = c.get("domain") or ""
        path = c.get("path") or "/"
        for d in (domain, domain.lstrip(".")):
            if not d:
                continue
            try:
                sess.session.cookies.set(name, value or "", domain=d, path=path)
            except Exception:
                pass


def refresh_and_check(sess: BrowserSession, cookies: list[dict], device_id: str) -> dict:
    """用 cookies 调 /api/auth/session 拿新 access_token,再 check。"""
    sess.device_id = device_id
    inject_cookies(sess, cookies)
    try:
        info = auth.fetch_session(sess)
    except Exception as exc:
        return {"status": "refresh_failed", "detail": f"{type(exc).__name__}: {str(exc)[:80]}"}
    at = info.get("accessToken")
    if not at:
        return {"status": "refresh_failed", "detail": "session 无 accessToken"}
    try:
        r = auth.check_account_health(sess, at)
        r["refreshed"] = True
        return r
    except Exception as exc:
        return {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:80]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="", help="只测某 sentinel 模式")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cfg = load_config()
    resolved = resolve_proxy(cfg)
    sess = BrowserSession(cfg, proxy=resolved.session_url)
    now = datetime.datetime.now()

    accounts = []
    for line in Path(ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not d.get("access_token"):
            continue
        so = d.get("sentinel_obs") or {}
        if args.mode and so.get("challenge_mode") != args.mode:
            continue
        accounts.append(d)
    if args.limit:
        accounts = accounts[-args.limit:]

    print(f"检查 {len(accounts)} 个账号 (now={now:%m-%d %H:%M})\n")
    rows = []
    for d in accounts:
        email = d.get("email")
        at = d.get("access_token")
        iat, exp = jwt_exp(at)
        cookies = d.get("session_cookies") or []
        so = d.get("sentinel_obs") or {}
        mode = so.get("challenge_mode")

        exp_s = f"{exp:%m-%d %H:%M}" if exp else "?"
        if cookies:
            r = refresh_and_check(sess, cookies, d.get("device_id") or "")
            status = r.get("status")
            detail = str(r.get("body") or r.get("detail") or "")[:40]
            print(f"  [刷新] {email} [{mode}] exp={exp_s} -> {status} {detail}")
            rows.append((email, mode, status))
        elif exp and exp > now:
            sess.device_id = d.get("device_id") or ""
            try:
                r = auth.check_account_health(sess, at)
                status = r.get("status")
                detail = str(r.get("body") or r.get("detail") or "")[:40]
            except Exception as exc:
                status = "error"
                detail = f"{type(exc).__name__}: {str(exc)[:40]}"
            print(f"  [直测] {email} [{mode}] exp={exp_s}(有效) -> {status} {detail}")
            rows.append((email, mode, status))
        else:
            print(f"  [未知] {email} [{mode}] exp={exp_s}(已过) 无刷新凭证,无法判定")
            rows.append((email, mode, "unknown_no_refresh"))
    resolved.close()

    ok = sum(1 for _, _, s in rows if s == "ok")
    dead = sum(1 for _, _, s in rows if s in ("invalidated", "refresh_failed"))
    unknown = sum(1 for _, _, s in rows if s.startswith("unknown"))
    print(f"\n存活 {ok}/{len(rows)}  真死/刷新失败 {dead}  未知 {unknown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

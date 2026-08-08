#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证: token 交换加 IMAP scope 后, IMAP 不可用账号能否恢复 IMAP。

背景: IMAPOAuthClient token 交换缺 scope → 部分 refresh_token 生成的 access_token
缺 IMAP.AccessAsUser.All 权限 → "authenticated but not connected" → 降级 Graph。
服务 outlook.xdauv.xyz 带 scope 能对全部账号收码 → 修复 = token 请求加 scope。

本脚本: 用降级账号(LeslieChavez) + 带 scope 换 token, 测试 IMAP 连接, 对比两端点。

用法: python capture/test_imap_scope.py [--email 账号关键字]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import curl_cffi.requests as cr
import imaplib
import json

SCOPES = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
ENDPOINTS = {
    "login.live.com": "https://login.live.com/oauth20_token.srf",
    "msonline/consumers": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
}


def _find_line(keyword: str) -> str:
    for l in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
        if keyword in l:
            return l.strip()
    raise RuntimeError(f"号池找不到 {keyword}")


def _token(endpoint: str, client_id: str, rt: str, with_scope: bool) -> str | None:
    data = {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": rt}
    if with_scope:
        data["scope"] = SCOPES
    try:
        r = cr.post(endpoint, data=data, timeout=25, impersonate="chrome",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        j = r.json()
        if r.status_code == 200 and j.get("access_token"):
            return j["access_token"]
        print(f"    token 失败 {r.status_code}: {str(j)[:120]}")
    except Exception as exc:
        print(f"    token 异常: {type(exc).__name__}: {str(exc)[:100]}")
    return None


def _imap_ok(email: str, at: str) -> bool:
    try:
        conn = imaplib.IMAP4_SSL("outlook.office365.com", 993)
        auth_str = f"user={email}\x01auth=Bearer {at}\x01\x01"
        conn.authenticate("XOAUTH2", lambda x: auth_str.encode())
        conn.select("INBOX", readonly=True)
        conn.logout()
        return True
    except Exception as exc:
        print(f"    IMAP 失败: {str(exc)[:100]}")
        return False


def main() -> int:
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="LeslieChavez")
    args = ap.parse_args()

    line = _find_line(args.email)
    parts = line.split("----")
    email, _, client_id, rt = parts[0], parts[1], parts[2], parts[3]
    print(f"账号: {email}  (IMAP 之前不可用)")
    print(f"refresh_token 长度: {len(rt)}")

    for name, ep in ENDPOINTS.items():
        for with_scope in (False, True):
            tag = "带scope" if with_scope else "无scope"
            print(f"\n[{name}] {tag}:")
            at = _token(ep, client_id, rt, with_scope)
            if at:
                ok = _imap_ok(email, at)
                print(f"    -> {'✅ IMAP 可用' if ok else '❌ IMAP 不可用'}")
            else:
                print(f"    -> token 未拿到")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

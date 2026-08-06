#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""新注册账号(fresh recent_auth) → 完整 dump mfa/enroll 响应 → 找 confirm 步骤 → 激活 2FA。

背景:
  - probe_enroll 用旧 at 调 enroll 得 401 recent_auth_required → enroll 必须新鲜 recent_auth
  - verify_pwd_totp 注册后 enroll 200 拿 secret, 但缺 confirm → mfa_enabled 仍 false
  - 本脚本: 注册全新账号(注册会话天然 fresh recent_auth) → enroll 完整 dump →
    提取 secret + 观察 enroll 响应里的 confirm 要求/factor_id → pyotp 码试 confirm →
    复查 mfa_info 确认 mfa_enabled: true

用法: python capture/probe_enroll_new.py [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import re
import string
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402


def _load_vpt():
    spec = importlib.util.spec_from_file_location("vpt_mod", ROOT / "capture" / "verify_pwd_totp.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["vpt_mod"] = m
    spec.loader.exec_module(m)
    return m


def _api_headers(s, referer: str = "https://chatgpt.com/") -> dict:
    h = s.chatgpt_headers(referer=referer)
    h["content-type"] = "application/json"
    return h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    vpt = _load_vpt()

    # 号池选主号(收码)
    account = None
    for line in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines():
        a = parse_mail_line(line.strip())
        if not a:
            continue
        account = a
        break
    if not account:
        print("号池找不到收码账号")
        return 1
    base_email = account["email"]
    name, dom = base_email.split("@")
    tag = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    email = f"{name}+{tag}@{dom}"
    password = "".join(random.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(14))
    display_name, bday = "James Miller", "1998-05-12"
    print(f"注册邮箱: {email}  密码: {password}  收码主号: {base_email}")

    # 1. 注册(fresh recent_auth 会话)
    # monkey-patch: 打印 create_account / register 的关键响应, 定位 400 根因
    _orig_post = BrowserSession.post

    def _patched_post(self, url, *a, **k):
        r = _orig_post(self, url, *a, **k)
        u = str(url)
        if "create_account" in u:
            print(f"    [dbg] POST create_account -> {r.status_code}")
            print(f"    [dbg] 完整 body: {(r.text or '')[:2000]}")
        elif any(m in u for m in ("user/register", "email-otp", "mfa")):
            print(f"    [dbg] POST {u[-45:]} -> {r.status_code}: {(r.text or '')[:200]}")
        return r

    BrowserSession.post = _patched_post
    t0 = time.time()
    args_p = SimpleNamespace(proxy=args.proxy)
    try:
        reg = vpt._register(cfg, args_p, account, email, password, display_name, bday, base_email)
    finally:
        BrowserSession.post = _orig_post
    print(f"[注册] 耗时 {(time.time()-t0):.0f}s")
    if not reg:
        print("[x] 注册失败")
        return 2
    print(f"[注册] at={reg['at'][:25]}...  t_len={reg['t_len']}  so_len={reg['so_len']}")

    # 2. 建 session + 注入 cookies(注册会话, fresh recent_auth)
    resolved = resolve_proxy(cfg, override=args.proxy)
    s = BrowserSession(cfg, proxy=resolved.session_url)
    s.device_id = reg["device_id"]
    for c in reg["cookies"]:
        for d in (c["domain"].lstrip("."), c["domain"]):
            if d:
                try:
                    s.session.cookies.set(c["name"], c["value"], domain=d, path=c["path"])
                except Exception:
                    pass
    h = _api_headers(s)
    h["authorization"] = f"Bearer {reg['at']}"
    h["oai-device-id"] = reg["device_id"]

    # 3. POST mfa/enroll → 完整 dump
    print("\n[1] POST mfa/enroll factor_type=totp")
    resp = s.post("https://chatgpt.com/backend-api/accounts/mfa/enroll",
                  headers=h, data=json.dumps({"factor_type": "totp"}), timeout=30)
    print(f"    -> {resp.status_code}")
    body = resp.text
    print(f"    body: {body[:2000]}")
    if resp.status_code != 200:
        print("[x] enroll 失败")
        resolved.close()
        return 3

    # 4. 解析 enroll 响应结构
    print("\n[2] enroll 响应结构")
    try:
        erj = json.loads(body)
        print(f"    顶层键: {list(erj.keys())}")
        for k, v in erj.items():
            vs = json.dumps(v, ensure_ascii=False)[:300] if not isinstance(v, str) else str(v)[:300]
            print(f"    {k}: {vs}")
    except Exception:
        print(f"    (非 JSON 文本, 前 {len(body)} 字符)")

    secret = None
    m_otp = re.search(r"otpauth://[^\s\"']+", body)
    m_sec = re.search(r"[A-Z2-7]{32}", body)
    if m_otp:
        secret = m_otp.group(0)
        m2 = re.search(r"[?&]secret=([A-Z2-7]+)", secret)
        if m2:
            secret = m2.group(1)
    elif m_sec:
        secret = m_sec.group(0)
    print(f"\n    secret: {secret}")
    if not secret:
        print("[x] 未提取到 secret")
        resolved.close()
        return 4

    # 5. pyotp 码 + confirm 候选实验
    import pyotp
    code6 = pyotp.TOTP(secret).now()
    print(f"\n[3] pyotp 6位码: {code6}")

    candidates = [
        ("totp/confirm", {"code": code6, "factor_type": "totp"}),
        ("enroll/confirm", {"code": code6, "factor_type": "totp"}),
        ("totp/verify", {"code": code6, "factor_type": "totp"}),
        ("confirm", {"code": code6, "factor_type": "totp"}),
        ("totp/confirm", {"code": code6}),
        ("enroll/confirm", {"code": code6}),
    ]
    for path, payload in candidates:
        url = f"https://chatgpt.com/backend-api/accounts/mfa/{path}"
        try:
            rc = s.post(url, headers=h, data=json.dumps(payload), timeout=30)
            print(f"    POST /mfa/{path} {list(payload.keys())} -> {rc.status_code}: {rc.text[:250]}")
        except Exception as exc:
            print(f"    POST /mfa/{path} 异常: {type(exc).__name__}: {str(exc)[:80]}")
        time.sleep(0.4)

    # 6. 复查 mfa_info
    print("\n[4] 复查 mfa_info")
    ri = s.get("https://chatgpt.com/backend-api/accounts/mfa_info", headers=h, timeout=30)
    print(f"    -> {ri.status_code}: {ri.text[:600]}")

    # 7. 输出
    from gptreg.store import save_account
    save_account(cfg, record={
        "email": email, "password": password, "totp_secret": secret,
        "status": "ok", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    print(f"[保存] {email} 已写入 accounts.jsonl(含 totp_secret)")
    print(f"[总耗时] {(time.time()-t0):.0f}s")
    resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

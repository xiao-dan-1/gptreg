#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""纯协议探针: 用已存 access_token+cookies 直接调 mfa 接口, 找 enroll→confirm 的完整 API。

不触发登录限流(不走 authorize/password)。目标:
  1. 确认 access_token 是否还有效(accounts/check)
  2. 调 mfa_info 看权威 MFA 状态
  3. POST mfa/enroll, dump 完整响应(verify_pwd_totp 只提取 secret, 可能忽略了 confirm 要求)
  4. 若响应含 confirm/factor_id/challenge 信息, 用 pyotp 码尝试 confirm → 激活 2FA

用法: python capture/probe_enroll.py [--email 账号关键字] [--proxy ...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402


def _find_account(email_contains: str) -> dict:
    for line in (ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if email_contains in d.get("email", ""):
            return d
    raise RuntimeError(f"未找到账号含 {email_contains}")


def _api_headers(s, referer: str = "https://chatgpt.com/") -> dict:
    h = s.chatgpt_headers(referer=referer)
    h["content-type"] = "application/json"
    return h


def _extract_secret(txt: str) -> str | None:
    m_otp = re.search(r"otpauth://[^\s\"']+", txt)
    if m_otp:
        m2 = re.search(r"[?&]secret=([A-Z2-7]+)", m_otp.group(0))
        if m2:
            return m2.group(1)
    m_sec = re.search(r"[A-Z2-7]{32}", txt)
    return m_sec.group(0) if m_sec else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="KathrynEverett6196")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()
    acc = _find_account(args.email)
    email = acc["email"]
    at = acc.get("access_token") or ""
    device_id = acc.get("device_id") or ""
    print(f"账号: {email}  at={at[:25]}...  device_id={device_id[:8]}")

    r = resolve_proxy(cfg, override=args.proxy)
    s = BrowserSession(cfg, proxy=r.session_url)
    s.device_id = device_id
    # 注入 cookies
    for c in (acc.get("session_cookies") or []):
        for d in (c["domain"].lstrip("."), c["domain"]):
            if d:
                try:
                    s.session.cookies.set(c["name"], c["value"], domain=d, path=c["path"])
                except Exception:
                    pass
    print(f"代理: {r.label()}  注入 cookies: {len(acc.get('session_cookies') or [])}")

    try:
        # 1. accounts/check(确认 at 有效)
        print("\n[1] accounts/check")
        try:
            health = auth.check_account_health(s, at)
            print(f"    -> {health.get('status')} {(str(health.get('body') or health.get('detail')) or '')[:120]}")
        except Exception as exc:
            print(f"    check 异常: {type(exc).__name__}: {str(exc)[:100]}")

        # 2. mfa_info
        print("\n[2] GET mfa_info")
        h = _api_headers(s)
        h["authorization"] = f"Bearer {at}"
        h["oai-device-id"] = device_id
        try:
            resp = s.get("https://chatgpt.com/backend-api/accounts/mfa_info", headers=h, timeout=30)
            print(f"    -> {resp.status_code}: {resp.text[:600]}")
        except Exception as exc:
            print(f"    mfa_info 异常: {type(exc).__name__}: {str(exc)[:100]}")

        # 3. POST mfa/enroll(完整响应)
        print("\n[3] POST mfa/enroll factor_type=totp")
        resp_e = s.post("https://chatgpt.com/backend-api/accounts/mfa/enroll",
                        headers=h, data=json.dumps({"factor_type": "totp"}), timeout=30)
        print(f"    -> {resp_e.status_code}")
        print(f"    body: {resp_e.text[:1800]}")
        if resp_e.status_code != 200:
            print("    [x] enroll 失败(可能 at 过期/recent_auth 不足), 停止")
            return 1

        # 4. 提取 secret + 尝试 confirm
        secret = _extract_secret(resp_e.text)
        if not secret:
            print("    [!] 未提取到 secret")
            return 2
        print(f"    secret: {secret}")
        try:
            import pyotp
            code6 = pyotp.TOTP(secret).now()
            print(f"    pyotp 6位码: {code6}")
        except ImportError:
            print("    pyotp 未安装, 跳过 confirm 实验")
            return 0

        # 5. confirm 候选 endpoint(逐个试)
        print("\n[4] confirm 候选 endpoint 实验")
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
                resp_c = s.post(url, headers=h, data=json.dumps(payload), timeout=30)
                print(f"    POST /mfa/{path} {payload} -> {resp_c.status_code}: {resp_c.text[:250]}")
            except Exception as exc:
                print(f"    POST /mfa/{path} 异常: {type(exc).__name__}: {str(exc)[:80]}")
            time.sleep(0.5)

        # 6. 最终状态
        print("\n[5] 复查 mfa_info")
        resp = s.get("https://chatgpt.com/backend-api/accounts/mfa_info", headers=h, timeout=30)
        print(f"    -> {resp.status_code}: {resp.text[:600]}")
    finally:
        r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

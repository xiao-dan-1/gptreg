#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码V3注册 + 立即开TOTP 连贯验证。

流程: verify_pwd_v3 注册(拿 cookies/新账号) → enable_totp 立即开 TOTP(recent_auth 新鲜)。
记录全程分阶段耗时, 便于分析。

用法: python capture/verify_pwd_totp_chain.py --email 主号
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    py = sys.executable
    t0 = time.time()

    # 1. 密码V3 注册(拿 cookies + password 落盘 accounts.jsonl)
    print("=" * 50)
    print("[阶段1] 密码V3 注册")
    t1 = time.time()
    cmd = [py, "-u", str(ROOT / "capture" / "verify_pwd_v3.py"),
           "--email", args.email, "--alias", "--proxy", args.proxy]
    r1 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out1 = (r1.stdout or "") + (r1.stderr or "")
    # 打印关键行
    for line in out1.splitlines():
        if any(k in line for k in ("注册邮箱", "已保存", "耗时", "失败", "HTTP", "OTP:", "✅", "[x]", "authorize")):
            print(f"  {line}")
    d1 = time.time() - t1
    print(f"  [阶段1 耗时] {d1:.1f}s (exit={r1.returncode})")
    if r1.returncode != 0 or "已保存" not in out1:
        print(f"[!] 阶段1 注册失败, 中止. 完整输出尾部: {out1[-500:]}")
        return 1

    # 2. 立即开 TOTP
    print("=" * 50)
    print("[阶段2] enable_totp (立即, recent_auth 新鲜)")
    t2 = time.time()
    cmd2 = [py, "-u", str(ROOT / "capture" / "enable_totp.py"),
            "--email", args.email.split("@")[0].split("+")[0], "--proxy", args.proxy]
    r2 = subprocess.run(cmd2, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out2 = (r2.stdout or "") + (r2.stderr or "")
    for line in out2.splitlines():
        if any(k in line for k in ("账号", "[耗时]", "TOTP", "secret", "安全页", "re-auth", "失败", "启用", "✅", "[x]", "[!]", "OTP:")):
            print(f"  {line}")
    d2 = time.time() - t2
    print(f"  [阶段2 耗时] {d2:.1f}s (exit={r2.returncode})")

    print("=" * 50)
    print(f"[总耗时] {time.time()-t0:.1f}s")
    return r2.returncode


if __name__ == "__main__":
    raise SystemExit(main())

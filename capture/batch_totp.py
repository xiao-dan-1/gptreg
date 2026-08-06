#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量生产真正激活的 TOTP 账号(用未用过主号逐个跑 verify_pwd_totp)。

verify_pwd_totp 已修复: enroll → activate_enrollment(2FA 真正激活, mfa_enabled:true)。
本脚本自动选「未用过主号」(accounts/pwd/totp/failed 记录里没有的), 逐个跑注册+enroll+activate。

⚠️ 连续同 IP 注册触发 OpenAI 风控(register 400 invalid_auth_step, 成功率 67%→20%)。
默认用 config.yaml 动态代理模板每次换 sid(新 IP) 分散风控; 可用 --proxy 手动固定。

用法: python capture/batch_totp.py [--limit 3] [--proxy http://127.0.0.1:10808] [--list] [--no-dynamic]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import string
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = ROOT / "output"
POOL = ROOT / "mail_pool.txt"
VPT = ROOT / "capture" / "verify_pwd_totp.py"
FAILED_FILE = ROOT / "data" / "totp_failed.txt"  # 注册失败主号, 下次批量跳过


def _used_mains() -> set[str]:
    """已用主号(accounts.jsonl 统一主库 + failed 记录)。"""
    used: set[str] = set()
    for line in (OUT / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line).get("email", "")
            used.add(e.split("@")[0].split("+")[0] + "@" + e.split("@")[-1])
        except Exception:
            pass
    if FAILED_FILE.exists():
        for line in FAILED_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                used.add(line)
    return used


def _unused_mains() -> list[str]:
    used = _used_mains()
    mains = []
    for line in POOL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        email = line.split("----")[0]
        main = email.split("@")[0].split("+")[0] + "@" + email.split("@")[-1]
        if main not in used:
            mains.append(main)
    return mains


def _load_dynamic_template() -> str:
    """从 config.yaml 读动态代理模板(http://...-region-US-sid-xxx-t-5:pass@host)。"""
    try:
        import yaml
        cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
        dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
        tpl = str(dyn.get("template") or "")
        region = str(dyn.get("region") or "")
        if region:
            tpl = re.sub(r"-region-[A-Za-z]+-", f"-region-{region}-", tpl)
        return tpl
    except Exception:
        return ""


def _new_proxy(tpl: str) -> str:
    """动态模板换随机 sid(新 IP), sticky 由模板 t-N 控制。"""
    sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return re.sub(r"-sid-[a-zA-Z0-9]+-t-", f"-sid-{sid}-t-", tpl)


def _run_one(main_email: str, proxy: str, idx: int, total: int) -> tuple[bool, str]:
    print(f"\n[{idx}/{total}] 主号 {main_email} ...")
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(VPT), "--email", main_email, "--proxy", proxy],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, f"异常 {type(exc).__name__}: {str(exc)[:60]}"

    dt = time.time() - t0
    # 成功判定: activate 200 + mfa_enabled True + 已保存
    ok = "[activate_enrollment] HTTP 200" in out and "mfa_enabled=True" in out and "已保存" in out
    secret_line = ""
    for line in out.splitlines():
        if "TOTP: " in line:
            secret_line = line.strip()
    # 打印关键行(压缩)
    key_lines = [l.strip() for l in out.splitlines()
                 if any(k in l for k in ("create_account] HTTP", "[mfa/enroll]", "[activate_enrollment]",
                                         "[mfa_info]", "TOTP:", "总耗时", "失败", "x]"))]
    for l in key_lines[:12]:
        print(f"    {l}")
    print(f"    -> {'成功' if ok else '失败'} ({dt:.0f}s) {secret_line}")
    return ok, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3, help="本次批量数量(默认 3)")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808", help="固定代理(不换 IP, 用于手动指定)")
    ap.add_argument("--no-dynamic", action="store_true", help="不用动态代理换 IP(默认用 config 动态模板)")
    ap.add_argument("--list", action="store_true", help="只列出未用过主号不跑")
    args = ap.parse_args()

    unused = _unused_mains()
    print(f"未用过主号: {len(unused)} 个")
    if args.list:
        for m in unused[:args.limit or None]:
            print("  ", m)
        return 0

    # 动态代理: 每次注册换 sid(新 IP), 分散 OpenAI 风控
    tpl = "" if args.no_dynamic else _load_dynamic_template()
    if tpl:
        print(f"动态代理: 每次换 IP ({'US' if 'region-US' in tpl else '模板'})")
    else:
        print(f"固定代理: {args.proxy} (连续注册可能触发 IP 风控)")

    batch = unused[:args.limit]
    results = []
    for i, m in enumerate(batch, 1):
        proxy = _new_proxy(tpl) if tpl else args.proxy
        ok, _ = _run_one(m, proxy, i, len(batch))
        results.append((m, ok))
        if not ok:
            # 失败主号记录, 下次跳过(register invalid_auth_step 等, 重试难成功)
            FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)
            with FAILED_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{m}\n")
        time.sleep(2)  # 间隔避免限流

    n_ok = sum(1 for _, ok in results if ok)
    print(f"\n批量完成: {n_ok}/{len(batch)} 成功")
    for m, ok in results:
        print(f"  {'✅' if ok else '❌'} {m}")
    if len(results) - n_ok:
        print(f"失败主号已记录 {FAILED_FILE} (下次跳过)")
    return 0 if n_ok == len(batch) else 1


if __name__ == "__main__":
    raise SystemExit(main())

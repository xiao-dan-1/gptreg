#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量生产 TOTP 账号：复用 gptreg/register_pwd.register_account(结构化结果), 不再 subprocess。

主号生命周期(修复原 subprocess 把所有失败烧号的问题):
  SUCCESS         → accounts.jsonl 落盘即标记已用(下次 _unused_mains 跳过)
  MAIL_REGISTERED → 永久弃用(写 data/totp_failed.txt)——邮箱已在 OpenAI 注册
  IP_BLOCKED/基建 → 不烧号(下次批量可重试); register 400 换 IP 自愈在核心内部

用法: python capture/batch_totp.py [--limit 3] [--proxy ...] [--no-dynamic] [--list]
"""
from __future__ import annotations

import argparse
import json
import random
import string
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config, random_birthdate, random_display_name  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.register_pwd import RegisterOutcome, register_account  # noqa: E402

OUT = ROOT / "output"
POOL = ROOT / "mail_pool.txt"
FAILED_FILE = ROOT / "data" / "totp_failed.txt"


def _base(m: str) -> str:
    """主号: x+tag@dom → x@dom(去 plus tag)。"""
    e = (m or "").strip()
    if "@" not in e:
        return e
    local, dom = e.rsplit("@", 1)
    return f"{local.split('+')[0]}@{dom}"


def _used_mains() -> set[str]:
    """已用主号(accounts.jsonl 主库 + 永久弃用记录)。"""
    used: set[str] = set()
    p = OUT / "accounts.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line).get("email", "")
                used.add(_base(e))
            except Exception:
                pass
    if FAILED_FILE.exists():
        for line in FAILED_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                used.add(_base(line))
    return used


def _unused_mains() -> list[tuple[str, dict]]:
    """[(主号, 号池行)]——未用过且未永久弃用的主号。"""
    used = _used_mains()
    # 池状态文件(bad=永久弃用)合并进 used, 覆盖账号表反查盲区(iCloud 池等)
    try:
        state_file = Path(str(POOL) + ".state.json")
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8")) or {}
            for e in (state.get("used") or []):
                used.add(_base(e))
            for e in (state.get("bad") or {}):
                used.add(_base(e))
    except Exception:
        pass
    mains: list[tuple[str, dict]] = []
    for line in POOL.read_text(encoding="utf-8").splitlines():
        a = parse_mail_line(line.strip())
        if not a:
            continue
        main = _base(a["email"])
        if main not in used:
            mains.append((main, a))
    return mains


def _alias_of(main: str) -> str:
    name, dom = main.split("@")
    tag = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"{name}+{tag}@{dom}"


def _mark_permanent(main: str) -> None:
    """永久弃用(邮箱已在 OpenAI 注册, 换 IP 无效)。"""
    FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FAILED_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{main}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3, help="本次批量数量(默认 3)")
    ap.add_argument("--pool", default="", help="号池文件(默认 mail_pool.txt; icloud 可用 icloud_pool.txt; cloudmail=动态生成)")
    ap.add_argument("--proxy", default=None, help="固定代理(--no-dynamic 时用; 默认走 config 动态链式)")
    ap.add_argument("--no-dynamic", action="store_true", help="不用动态代理换 IP(用 --proxy 固定)")
    ap.add_argument("--list", action="store_true", help="只列出未用过主号不跑")
    args = ap.parse_args()

    cfg = load_config()
    # CloudMail: 动态生成邮箱(不依赖号池文件)
    if args.pool == "cloudmail":
        from gptreg.mail.cloudmail import generate_email

        batch = []
        for _ in range(args.limit):
            acct = generate_email(cfg)
            batch.append((acct["email"], acct))
        print(f"CloudMail 动态生成 {len(batch)} 个邮箱")
        if args.list:
            for main, _ in batch:
                print("  ", main)
            return 0
        proxy = None if not args.no_dynamic else args.proxy
        return _run_batch(batch, proxy, cfg)

    # 号池文件(默认 mail_pool.txt; --pool 支持 icloud 快捷名)
    pool_file = args.pool or "mail_pool.txt"
    if pool_file == "icloud":
        pool_file = "icloud_pool.txt"
    global POOL
    POOL = ROOT / pool_file

    unused = _unused_mains()
    print(f"未用过主号: {len(unused)} 个")
    if args.list:
        for main, _ in unused[: args.limit or None]:
            print("  ", main)
        return 0

    proxy = None if not args.no_dynamic else args.proxy
    if proxy:
        print(f"固定代理: {proxy}")
    else:
        print("动态代理: 每次换 sid(新出口), register 400 自动换 IP 重试")

    batch = unused[: args.limit]
    return _run_batch(batch, proxy, cfg)


def _run_batch(batch: list[tuple[str, dict]], proxy, cfg) -> int:
    """批量注册: batch=[(主号, account)]。"""
    results: list[tuple[str, bool, RegisterOutcome]] = []
    for i, (main, account) in enumerate(batch, 1):
        t0 = time.time()
        # iCloud/cloudmail 一邮箱一账号: 用主邮箱(URL绑定, alias 收码不可靠); 其余走别名
        if account.get("mail_type") in ("icloud", "cloudmail"):
            email = main
        else:
            email = _alias_of(main)
        password = "".join(random.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(14))
        display_name = random_display_name()
        bday = random_birthdate(cfg)
        print(f"\n[{i}/{len(batch)}] 主号 {main} ...")
        result = register_account(
            cfg, account,
            email=email, password=password, name=display_name, bday=bday,
            proxy=proxy,
        )
        dt = time.time() - t0
        ok = result.outcome == RegisterOutcome.SUCCESS
        if ok:
            print(f"  注册邮箱: {email}  身份: {display_name}")
            print(f"  -> 成功 ({dt:.0f}s) TOTP: {((result.record or {}).get('totp_secret') or '')[:12]}...")
        else:
            reason = result.diag.get("landing_diag") or result.diag.get("reason", "")
            print(f"  注册邮箱: {email}  身份: {display_name}")
            print(f"  [{result.outcome.value}] {str(reason)[:100]}")
            print(f"  -> 失败 ({dt:.0f}s)")
        # 主号生命周期
        if result.outcome == RegisterOutcome.MAIL_REGISTERED:
            _mark_permanent(main)
            print(f"  [永久弃用] 邮箱已注册, 已记 {FAILED_FILE.name}")
        # SUCCESS 由 accounts.jsonl 落盘标记已用; 其他失败(IP_BLOCKED/基建)不烧号, 下次可重试
        results.append((main, ok, result.outcome))
        time.sleep(1)

    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"\n批量完成: {n_ok}/{len(batch)} 成功")
    for main, ok, oc in results:
        print(f"  [{'OK' if ok else 'X'}] {main} ({oc.value})")
    return 0 if n_ok == len(batch) else 1


if __name__ == "__main__":
    raise SystemExit(main())

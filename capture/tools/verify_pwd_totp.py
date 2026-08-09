#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码注册 + TOTP 2FA 激活 —— CLI 薄壳。

实际注册链在 gptreg/register_pwd.register_account(结构化结果)。
本脚本只做: 选主号 / 生成别名+密码+姓名 / 调核心 / 按 outcome 打印反馈。

用法: python capture/verify_pwd_totp.py --email 主号 [--alias|--no-alias] [--proxy ...]
"""
from __future__ import annotations

import random
import string
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):  # 中文 logger 走 stderr, 也必须 UTF-8(否则 cp936 乱码)
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config, random_birthdate, random_display_name  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.register_pwd import RegisterOutcome, register_account, timing_str  # noqa: E402


def _base(m: str) -> str:
    """主号用户名: x+tag@dom → x, 便于 --email 裸用户名匹配号池。"""
    return (m or "").split("@")[0].split("+")[0]




def main() -> int:
    import argparse as _ap
    import logging as _logging

    # 完整日志: 让 IMAP 降级/Graph 索引进度等 logger 输出可见(默认 lastResort 只显示 WARNING)
    # format 带级别前缀: 排查时区分 INFO/WARNING/ERROR(日志问题非小事)
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--pool", default="", help="号池文件(默认 mail_pool.txt; icloud 可用 icloud_pool.txt)")
    ap.add_argument("--alias", action="store_true", help="强制用 plus 别名注册(默认走 config mail.use_alias)")
    ap.add_argument("--no-alias", action="store_true", help="禁用别名, 用主号直接注册")
    ap.add_argument("--proxy", default=None, help="覆盖代理(默认走 config 动态链式, 勿用 10808 僵尸端口)")
    args = ap.parse_args()

    cfg = load_config()
    t0 = time.time()

    # CloudMail: 动态生成邮箱(不依赖号池文件)
    if args.pool == "cloudmail":
        from gptreg.mail.cloudmail import generate_email

        account = generate_email(cfg)
        base_email = account["email"]
        print(f"CloudMail 动态生成: {base_email}")
    else:
        # 号池文件(默认 mail_pool.txt; --pool 支持 icloud 快捷名)
        pool_file = args.pool or "mail_pool.txt"
        if pool_file == "icloud":
            pool_file = "icloud_pool.txt"
        if not Path(pool_file).exists():
            print(f"号池文件不存在: {pool_file}")
            return 1

        # 号池选主号(收码身份)
        account = None
        for line in Path(pool_file).read_text(encoding="utf-8").splitlines():
            a = parse_mail_line(line.strip())
            if not a:
                continue
            if args.email and _base(a["email"]) != _base(args.email):
                continue
            account = a
            break
        if not account:
            print("号池找不到收码账号")
            return 1
        base_email = account["email"]

    # 默认 plus 别名(config mail.use_alias=true)——号池主号很多已在 OpenAI 注册,
    # 主号直接注册会落 email-verification/log-in → register 400; 别名是全新邮箱。
    use_alias = bool(cfg.get("mail", {}).get("use_alias", True))
    if account.get("mail_type") in ("cloudmail", "icloud"):
        use_alias = False  # 一邮箱一账号号源: 注册用主邮箱(不用 +tag 别名)
    if args.no_alias:
        use_alias = False
    elif args.alias:
        use_alias = True
    if use_alias:
        name, dom = base_email.split("@")
        tag = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        email = f"{name}+{tag}@{dom}"
    else:
        email = base_email
    password = "".join(random.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(14))
    display_name = random_display_name()
    bday = random_birthdate(cfg)
    print(f"注册邮箱: {email} (主号: {base_email}, {'别名' if use_alias else '直接用主号'})  密码: {password}")
    print(f"注册身份: {display_name} / {bday}")

    result = register_account(
        cfg, account,
        email=email, password=password, name=display_name, bday=bday,
        proxy=args.proxy,
    )
    d = result.diag

    if result.outcome == RegisterOutcome.SUCCESS:
        # 段增量归因(signin+register+otp+create+session+health+enroll = 总耗时), 与 batch 共用
        ts = timing_str(d)
        if ts:
            print(f"[耗时] {ts}")
        if d.get("send_otp") is not None:
            print(f"[OTP/send] send_otp HTTP {d['send_otp']}")
        elif "t_s" in d:
            print(f"[create/timing] quickjs t={d.get('t_s')}s so={d.get('so_s')}s 并行总={d.get('create_parallel')}s")
        if result.record:
            rec = result.record
            secret = rec.get("totp_secret") or ""
            print("[落盘] 账号已保存到 accounts.jsonl(含 totp_secret)")
            print("\n" + "=" * 50)
            print(f"账号: {email}")
            print(f"密码: {password}")
            print(f"TOTP: {secret}")
            print(f"otpauth: otpauth://totp/ChatGPT:{email}?secret={secret}&issuer=ChatGPT")
            print("=" * 50)
            # 出口代理脱敏(host/region/sid, 不含密码), IP 信誉归因用
            from gptreg.proxyutil import proxy_label as _pl
            pu = rec.get("proxy_used") or ""
            if pu:
                print(f"[出口] {_pl(pu)}")
        print(f"[总耗时] {(time.time()-t0):.1f}s")
        return 0

    if result.outcome == RegisterOutcome.IP_BLOCKED:
        print(f"[x] register 被拒(IP 风控): {str(d.get('reason', ''))[:80]}")
        ld = d.get("landing_diag")
        if ld:
            print(f"[register/诊断] {ld}")
        # 服务器原始 code(区分 IP 信誉 vs session 未推进等)
        if d.get("srv_code"):
            print(f"[register/服务器] code={d.get('srv_code')} redirect={d.get('srv_redirect','')[:60]}")
        ts = timing_str(d)
        if ts:
            print(f"[耗时] {ts}")
        print(f"[总耗时] {(time.time()-t0):.1f}s")
        return 2

    if result.outcome == RegisterOutcome.MAIL_REGISTERED:
        print(f"[x] 邮箱已注册(永久弃用): {d.get('landing_diag') or d.get('reason', '')[:80]}")
        # 服务器原始 code/redirect 佐证已注册(如 invalid_auth_step + login_with)
        if d.get("srv_code"):
            print(f"[register/服务器] code={d.get('srv_code')} redirect={d.get('srv_redirect','')[:60]}")
        print(f"[总耗时] {(time.time()-t0):.1f}s")
        return 2

    if result.outcome == RegisterOutcome.ENROLL_FAILED:
        print(f"[warn] 账号已建但 2FA 未激活: {str(d.get('reason', ''))[:100]}")
        if result.record:
            print(f"[落盘] 已保存 registered_no_totp: {result.record.get('email')}")
        ts = timing_str(d)
        if ts:
            print(f"[耗时] {ts}")
        print(f"[总耗时] {(time.time()-t0):.1f}s")
        return 3

    print(f"[x] 注册失败[{result.outcome.value}]: {str(d.get('reason', ''))[:120]}")
    ts = timing_str(d)
    if ts:
        print(f"[耗时] {ts}")
    print(f"[总耗时] {(time.time()-t0):.1f}s")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

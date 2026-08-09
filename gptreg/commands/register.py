"""register: OTP-only 注册（从原 cli.py 迁入，Phase 2 换密码+TOTP）。"""
from __future__ import annotations

import logging
from typing import Any

from gptreg.commands.common import apply_region, resolve_proxy_arg
from gptreg.proxyutil import resolve_proxy
from gptreg.register_otp import classify_result, format_bucket_summary, run_batch, summarize_buckets


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "register",
        help="注册账号（OTP-only，Phase 2 换密码+TOTP 主路线）",
    )
    p.add_argument("-n", "--count", type=int, default=1, help="注册数量，默认 1")
    p.add_argument("-w", "--workers", type=int, default=1, help="并发线程数，默认 1")
    p.add_argument("--delay", type=float, default=0.0, help="任务间隔秒数")
    p.add_argument("--continue-on-fail", action="store_true", help="失败后继续")
    p.add_argument("--proxy", default=None, help="覆盖代理；传 empty/none/direct 表示直连")
    p.add_argument("--no-proxy", action="store_true", help="强制直连")
    p.add_argument("--region", default=None, help="动态代理地区，如 US/JP/NL（覆盖 config）")
    p.add_argument("--pool", default=None, help="邮箱号池路径，覆盖配置")
    p.add_argument(
        "--sentinel-source",
        default=None,
        choices=["pow", "browser", "node", "quickjs",
                 "browser_t_quickjs_so", "quickjs_t_browser_so",
                 "quickjs_pwd_v3"],
        help=(
            "create 阶段 sentinel：pow=纯 Python（默认，通常无 so）| "
            "browser=真 Chrome token+so（opt-in；OTP 仍 pow）。"
        ),
    )
    p.add_argument(
        "--no-so",
        action="store_true",
        help="create 剥掉 openai-sentinel-so-token 头（隔离实验：判断 so 对存活的影响）",
    )
    p.set_defaults(func=run)


def run(cfg: dict[str, Any], args) -> int:
    """OTP-only 批量注册 + 分桶汇总（行为与原 cli.main 一致）。"""
    if args.pool:
        cfg.setdefault("mail", {})["pool_file"] = args.pool
    apply_region(cfg, args.region)
    if getattr(args, "sentinel_source", None):
        cfg.setdefault("protocol", {})["sentinel_source"] = args.sentinel_source
    if getattr(args, "no_so", False):
        cfg.setdefault("register", {})["no_so"] = True

    proxy_override = resolve_proxy_arg(args)

    if args.count < 1:
        logging.error("count 必须 >= 1")
        return 2
    if args.workers < 1:
        logging.error("workers 必须 >= 1")
        return 2
    if args.workers > args.count:
        args.workers = args.count

    # 预览一条代理（会消耗一个随机 sid，仅日志）
    preview = resolve_proxy(cfg, proxy_override)
    sentinel_src = str((cfg.get("protocol") or {}).get("sentinel_source") or "pow")
    try:
        logging.info(
            "配置: count=%s workers=%s proxy=%s pool=%s sentinel_source=%s",
            args.count, args.workers, preview.label(),
            cfg.get("mail", {}).get("pool_file"), sentinel_src,
        )
    finally:
        preview.close()

    results = run_batch(
        cfg,
        count=args.count,
        workers=args.workers,
        delay=args.delay,
        continue_on_fail=args.continue_on_fail,
        proxy=proxy_override,
    )
    ok = sum(1 for r in results if r.get("success"))
    bucket_summary = summarize_buckets(results)
    logging.info("[汇总] 成功 %s / 尝试 %s / 目标 %s", ok, len(results), args.count)
    logging.info("[汇总/分桶] %s", format_bucket_summary(bucket_summary))
    for r in results:
        if r.get("success"):
            logging.info("  ✓ %s  %s", r.get("email"), r.get("proxy_label") or "")
        else:
            bucket = r.get("fail_bucket") or classify_result(r)
            logging.info("  ✗ [%s] %s — %s", bucket, r.get("email") or "?", r.get("error"))
    return 0 if ok == args.count else 1

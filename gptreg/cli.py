"""命令行入口。"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gptreg import __version__
from gptreg.config import load_config, resolve_path
from gptreg.mail.pool import MailPool
from gptreg.register_otp import classify_result, format_bucket_summary, run_batch, summarize_buckets
from gptreg.proxyutil import proxy_label, resolve_proxy


def _banner() -> str:
    return (
        "\n"
        "  ╔══════════════════════════════════════╗\n"
        "  ║     GPT 协议注册机  v{ver:<8}       ║\n"
        "  ║     default pow · browser opt-in     ║\n"
        "  ╚══════════════════════════════════════╝\n"
    ).format(ver=__version__)


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("curl_cffi").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gptreg",
        description="ChatGPT / OpenAI 纯协议注册机（OTP-only）",
    )
    p.add_argument("-n", "--count", type=int, default=1, help="注册数量，默认 1")
    p.add_argument("-w", "--workers", type=int, default=1, help="并发线程数，默认 1")
    p.add_argument("--delay", type=float, default=0.0, help="任务间隔秒数")
    p.add_argument("--continue-on-fail", action="store_true", help="失败后继续")
    p.add_argument("--proxy", default=None, help="覆盖代理；传 empty 表示直连")
    p.add_argument("--no-proxy", action="store_true", help="强制直连")
    p.add_argument("--region", default=None, help="动态代理地区，如 US/JP/NL（覆盖 config）")
    p.add_argument("--config", default=None, help="配置文件路径，默认 config.yaml")
    p.add_argument("--pool", default=None, help="邮箱号池路径，覆盖配置")
    p.add_argument("--stats", action="store_true", help="只显示号池统计后退出")
    p.add_argument(
        "--check-proxy",
        action="store_true",
        help="检测当前代理出口 IP（动态代理会随机 sid + 链式隧道）",
    )
    p.add_argument(
        "--check-proxy-times",
        type=int,
        default=1,
        help="连续检测次数，用于验证换 sid 是否换 IP（默认 1）",
    )
    p.add_argument(
        "--sentinel-source",
        default=None,
        choices=["pow", "browser", "node", "quickjs",
                 "browser_t_quickjs_so", "quickjs_t_browser_so",
                 "quickjs_pwd_v3"],
        help=(
            "create 阶段 sentinel：pow=纯 Python（默认，通常无 so）| "
            "browser=真 Chrome token+so（opt-in；OTP 仍 pow）。"
            "P1：本环境 2h 有/无 so 双活，见 capture/p1-so-survival-20260712/FINDINGS.md"
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    p.add_argument(
        "--no-so",
        action="store_true",
        help="create 剥掉 openai-sentinel-so-token 头（隔离实验：判断 so 对存活的影响）",
    )
    p.add_argument("--version", action="version", version=f"gptreg {__version__}")
    return p


def _resolve_proxy_arg(args: argparse.Namespace) -> str | None:
    if args.no_proxy:
        return ""
    if args.proxy is None:
        return None
    if args.proxy.strip().lower() in {"empty", "none", "direct", ""}:
        return ""
    return args.proxy.strip()


def _apply_region(cfg: dict, region: str | None) -> None:
    if not region:
        return
    dyn = cfg.setdefault("proxy", {}).setdefault("dynamic", {})
    dyn["region"] = region.strip().upper()
    if not dyn.get("enabled"):
        # 用户显式指定 region 时，自动打开动态代理（若有 template）
        if dyn.get("template") or dyn.get("user"):
            dyn["enabled"] = True


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    print(_banner())

    cfg = load_config(args.config)
    if args.pool:
        cfg.setdefault("mail", {})["pool_file"] = args.pool
    _apply_region(cfg, args.region)
    if getattr(args, "sentinel_source", None):
        cfg.setdefault("protocol", {})["sentinel_source"] = args.sentinel_source
    if getattr(args, "no_so", False):
        cfg.setdefault("register", {})["no_so"] = True

    proxy_override = _resolve_proxy_arg(args)

    if args.check_proxy:
        return _cmd_check_proxy(cfg, proxy_override, times=max(1, args.check_proxy_times))
    if args.stats:
        return _cmd_stats(cfg)

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
            args.count,
            args.workers,
            preview.label(),
            cfg.get("mail", {}).get("pool_file"),
            sentinel_src,
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
    logging.info(
        "[汇总] 成功 %s / 尝试 %s / 目标 %s",
        ok,
        len(results),
        args.count,
    )
    logging.info("[汇总/分桶] %s", format_bucket_summary(bucket_summary))
    for r in results:
        if r.get("success"):
            logging.info("  ✓ %s  %s", r.get("email"), r.get("proxy_label") or "")
        else:
            bucket = r.get("fail_bucket") or classify_result(r)
            logging.info(
                "  ✗ [%s] %s — %s",
                bucket,
                r.get("email") or "?",
                r.get("error"),
            )
    return 0 if ok == args.count else 1


def _cmd_stats(cfg: dict) -> int:
    pool_path = resolve_path(cfg.get("mail", {}).get("pool_file", "mail_pool.txt"), Path(cfg["_root"]))
    if not pool_path.exists():
        logging.error("号池不存在: %s", pool_path)
        logging.info("可复制 mail_pool.txt.example 为 mail_pool.txt 后填入邮箱")
        return 1
    pool = MailPool(pool_path)
    n = pool.load()
    st = pool.stats()
    logging.info("号池: %s", pool_path)
    logging.info(
        "  total=%s unused=%s used=%s bad=%s in_flight=%s retrying=%s",
        st["total"],
        st["unused"],
        st["used"],
        st["bad"],
        st["in_flight"],
        st["retrying"],
    )
    logging.info("已加载 %s 条记录", n)
    return 0


def _cmd_check_proxy(cfg: dict, proxy_override: str | None, times: int = 1) -> int:
    from gptreg.proxyutil import probe_proxy

    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    if dyn.get("enabled"):
        logging.info(
            "动态代理: region=%s rotate_sid=%s chain_via=%s",
            dyn.get("region"),
            dyn.get("rotate_sid"),
            dyn.get("chain_via") or "(无)",
        )
    ok_n = 0
    ips: list[str] = []
    for i in range(times):
        resolved = resolve_proxy(cfg, proxy_override)
        try:
            logging.info(
                "[%s/%s] 检测: %s",
                i + 1,
                times,
                resolved.label(),
            )
            if resolved.session_url and resolved.chain:
                logging.info("        本地隧道: %s", resolved.session_url)
            info = probe_proxy(resolved.session_url, timeout=25)
            ip = info.get("ip") or ""
            ips.append(ip)
            extra = ""
            ipinfo = info.get("ipinfo") or {}
            if ipinfo:
                extra = f" country={ipinfo.get('country')} city={ipinfo.get('city')} org={ipinfo.get('org')}"
            logging.info(
                "        出口: status=%s ip=%s%s",
                info.get("status"),
                ip,
                extra,
            )
            if info.get("status") == 200 and ip:
                ok_n += 1
        except Exception as exc:
            logging.error("[%s/%s] 代理检测失败: %s", i + 1, times, exc)
        finally:
            resolved.close()
    if times > 1:
        unique = len(set(x for x in ips if x))
        logging.info("换 IP 统计: 成功 %s/%s，不同 IP %s 个 → %s", ok_n, times, unique, ips)
    return 0 if ok_n == times else 1


if __name__ == "__main__":
    sys.exit(main())

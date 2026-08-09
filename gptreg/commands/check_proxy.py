"""check-proxy: 检测代理出口 IP（验证换 sid 是否换 IP）。从原 cli.py 迁入。"""
from __future__ import annotations

import logging
from typing import Any

from gptreg.commands.common import apply_region, resolve_proxy_arg
from gptreg.proxyutil import probe_proxy, resolve_proxy


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("check-proxy", help="检测代理出口 IP（验证换 sid 换 IP）")
    p.add_argument("--proxy", default=None, help="覆盖代理；传 empty/none/direct 表示直连")
    p.add_argument("--no-proxy", action="store_true", help="强制直连")
    p.add_argument("--region", default=None, help="动态代理地区，如 US/JP/NL（覆盖 config）")
    p.add_argument("--times", type=int, default=1, help="连续检测次数，验证换 sid 是否换 IP（默认 1）")
    p.set_defaults(func=run)


def run(cfg: dict[str, Any], args) -> int:
    apply_region(cfg, args.region)
    proxy_override = resolve_proxy_arg(args)

    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    if dyn.get("enabled"):
        logging.info(
            "动态代理: region=%s rotate_sid=%s chain_via=%s",
            dyn.get("region"), dyn.get("rotate_sid"), dyn.get("chain_via") or "(无)",
        )

    times = max(1, int(args.times or 1))
    ok_n = 0
    ips: list[str] = []
    for i in range(times):
        resolved = resolve_proxy(cfg, proxy_override)
        try:
            logging.info("[%s/%s] 检测: %s", i + 1, times, resolved.label())
            if resolved.session_url and resolved.chain:
                logging.info("        本地隧道: %s", resolved.session_url)
            info = probe_proxy(resolved.session_url, timeout=25)
            ip = info.get("ip") or ""
            ips.append(ip)
            extra = ""
            ipinfo = info.get("ipinfo") or {}
            if ipinfo:
                extra = f" country={ipinfo.get('country')} city={ipinfo.get('city')} org={ipinfo.get('org')}"
            logging.info("        出口: status=%s ip=%s%s", info.get("status"), ip, extra)
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

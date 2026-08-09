"""命令行入口（统一 CLI 路由器）。

子命令分发: gptreg/commands/ 包内每命令 add_parser + run(cfg, args) -> int。
根级选项 --config/-v/--version 须置于子命令前; banner 打 stderr(不污染管道输出)。
"""
from __future__ import annotations

import argparse
import logging
import sys

from gptreg import __version__
from gptreg.config import load_config


def _banner() -> str:
    return (
        "\n"
        "  ╔══════════════════════════════════════╗\n"
        "  ║     GPTReg  v{ver:<8}              ║\n"
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
        description="GPTReg统一入口",
    )
    p.add_argument("--config", default=None, help="配置文件路径（须置于子命令前）")
    p.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    p.add_argument("--version", action="version", version=f"gptreg {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>", help="可用命令（--help 查看）")
    from gptreg.commands import add_all_parsers

    add_all_parsers(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    # UTF-8 输出（Windows cp936 控制台）——须在 parse/help 之前，否则中文 help 乱码
    # line_buffering: stdout 块缓冲会把 print 挤到进程结束, 与 stderr 即时 logger 分离错乱
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):  # 无子命令 → 完整 help
        parser.print_help()
        return 2
    configure_logging(args.verbose)
    cfg = load_config(args.config)  # 统一加载一次，命令 run 直接复用
    print(_banner(), file=sys.stderr)  # banner 打 stderr，不污染管道输出
    return args.func(cfg, args)  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(main())

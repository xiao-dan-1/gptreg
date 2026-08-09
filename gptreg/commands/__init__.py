"""CLI 子命令注册表(gptreg 统一入口的分发层)。

命令模块接口: add_parser(subparsers) 注册 argparse + run(cfg, args) -> int。
register/check-proxy/stats 从原 cli.py 迁入; 其余从 capture/tools 收编。

纪律(硬约束): 本模块只做注册与分发, 绝不顶层 import 重依赖——
  playwright / register_pwd / browser_sentinel 保持 lazy(如 browser_sentinel 内部
  playwright 是函数内 import)。未来接 Playwright 的命令必须在 run() 内 lazy import。
  验证: python -c "import sys,importlib; importlib.import_module('gptreg.commands'); \
        assert 'playwright' not in sys.modules"
"""
from __future__ import annotations

import importlib
from typing import Any

# 命令名 → 模块路径(每个模块须实现 add_parser + run(cfg, args) -> int)
COMMANDS: dict[str, str] = {
    "register": "gptreg.commands.register",
    "check-proxy": "gptreg.commands.check_proxy",
    "stats": "gptreg.commands.stats",
    "overview": "gptreg.commands.overview",
    "export": "gptreg.commands.export_accounts",
    "survival": "gptreg.commands.survival",
    "refresh": "gptreg.commands.refresh_at",
    "backfill": "gptreg.commands.backfill",
    "imap": "gptreg.commands.check_imap",
    "raw-check": "gptreg.commands.raw_check",
}


def add_all_parsers(subparsers: Any) -> None:
    """为全部命令注册 parser。importlib 按需加载, 避免 import 所有命令模块。"""
    for _name, _mod in COMMANDS.items():
        importlib.import_module(_mod).add_parser(subparsers)

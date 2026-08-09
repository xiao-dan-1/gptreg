"""stats: 号池统计。从原 cli.py 迁入。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gptreg.config import resolve_path
from gptreg.mail.pool import MailPool


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("stats", help="显示号池统计")
    p.add_argument("--pool", default=None, help="号池路径，覆盖配置")
    p.set_defaults(func=run)


def run(cfg: dict[str, Any], args) -> int:
    pool_path = resolve_path(
        args.pool or cfg.get("mail", {}).get("pool_file", "mail_pool.txt"),
        Path(cfg["_root"]),
    )
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
        st["total"], st["unused"], st["used"], st["bad"], st["in_flight"], st["retrying"],
    )
    logging.info("已加载 %s 条记录", n)
    return 0

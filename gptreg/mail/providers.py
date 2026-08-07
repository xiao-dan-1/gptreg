"""邮箱 OTP 客户端工厂（插件化: 来源 MAIL_SOURCES / 收码 MAIL_CLIENTS）。

核心只认接口:
  - build_mail_client 按 mail_type 查 MAIL_SOURCES(来源插件) → 该来源 build_client
  - MAIL_CLIENTS 是收码通道注册表(MailClient 插件)
新增号源/收码通道均注册进 sources.py 注册表, 本模块零改动。
"""
from __future__ import annotations

from typing import Any

from gptreg.mail.base import MailClient
from gptreg.mail.external import GmailApiClient, XDAuvMailClient
from gptreg.mail.imap import IMAPOAuthClient
from gptreg.mail.otp_cache import (
    IMAP_OPENAI_SENDER,
    IMAP_SCOPE,
    IMAP_TOKEN_ENDPOINT,
    MAIL_ENDPOINT,
    TOKEN_ENDPOINT,
    MailClientError,
    UsedCodeCache,
    mail_identity_key,
)
from gptreg.mail.sources import MAIL_CLIENTS, MAIL_SOURCES

__all__ = [
    "MAIL_CLIENTS",
    "MAIL_SOURCES",
    "MailClient",
    "IMAPOAuthClient",
    "GmailApiClient",
    "XDAuvMailClient",
    "MailClientError",
    "UsedCodeCache",
    "mail_identity_key",
    "build_mail_client",
]


def build_mail_client(
    account: dict[str, Any],
    proxy: str | None = None,
    impersonate: str = "chrome142",
    cfg: dict[str, Any] | None = None,
) -> MailClient:
    """按 mail_type 从 MAIL_SOURCES(来源插件) 构建收码客户端。

    cfg 传入后 use_xdauv 等用调用方配置, 而非重读磁盘 load_config。
    """
    mail_type = account.get("mail_type") or "ms_oauth"
    src = MAIL_SOURCES.get(mail_type)
    if src is None:
        raise ValueError(f"未知邮箱来源: {mail_type}")
    return src.build_client(account, proxy=proxy, impersonate=impersonate, cfg=cfg)

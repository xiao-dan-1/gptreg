"""邮箱 OTP 客户端工厂（插件化: 通道注册到 MAIL_CLIENTS, 核心只认接口）。

对齐 sentinel_engine 注册表模式——新增收码通道:
  1. 继承 gptreg.mail.base.MailClient 并实现 wait_for_otp
  2. 注册进 MAIL_CLIENTS
  核心 build_mail_client 不改(开闭原则)。
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

# 收码通道注册表(插件化: 新增通道在此注册, 工厂按 mail_type 查)
MAIL_CLIENTS: dict[str, type[MailClient]] = {
    "gmail_api": GmailApiClient,
    "ms_oauth": IMAPOAuthClient,  # 本地 IMAP(use_xdauv=false)
    "ms_oauth_xdauv": XDAuvMailClient,  # 服务收码(use_xdauv=true)
}


def build_mail_client(
    account: dict[str, Any],
    proxy: str | None = None,
    impersonate: str = "chrome142",
    cfg: dict[str, Any] | None = None,
) -> MailClient:
    """按 mail_type 选收码通道(从注册表查)。cfg 传入后 use_xdauv 用调用方配置,
    而非重读磁盘 load_config(修复 CLI --config 被静默忽略)。"""
    mail_type = account.get("mail_type") or "ms_oauth"
    if mail_type == "gmail_api":
        return MAIL_CLIENTS["gmail_api"](account, proxy=proxy, impersonate=impersonate)
    use_cfg = cfg
    if use_cfg is None:
        try:
            from gptreg.config import load_config
            use_cfg = load_config()
        except Exception:
            use_cfg = {}
    if (use_cfg.get("mail") or {}).get("use_xdauv", True):
        return MAIL_CLIENTS["ms_oauth_xdauv"](account, proxy=proxy, impersonate=impersonate, cfg=use_cfg)
    # ms_oauth 走 IMAP(XOAUTH2)，绕开 Graph ~150s 索引延迟
    return MAIL_CLIENTS["ms_oauth"](account, proxy=proxy, impersonate=impersonate, cfg=use_cfg)

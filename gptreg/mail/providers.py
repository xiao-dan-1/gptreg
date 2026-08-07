"""邮箱 OTP 客户端工厂（拆分后: 各通道在 imap/ms_graph/external/otp_cache）。"""
from __future__ import annotations

from typing import Any

from gptreg.mail.external import GmailApiClient, XDAuvMailClient
from gptreg.mail.imap import IMAPOAuthClient
from gptreg.mail.ms_graph import MSMailClient
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

def build_mail_client(
    account: dict[str, Any],
    proxy: str | None = None,
    impersonate: str = "chrome142",
    cfg: dict[str, Any] | None = None,
) -> MSMailClient | GmailApiClient | IMAPOAuthClient | XDAuvMailClient:
    """按 mail_type 选收码通道。cfg 传入后 use_xdauv/chain_via 用调用方配置,
    而非重读磁盘 load_config(修复 CLI --config 被静默忽略)。"""
    mail_type = account.get("mail_type") or "ms_oauth"
    if mail_type == "gmail_api":
        return GmailApiClient(account, proxy=proxy, impersonate=impersonate)
    use_cfg = cfg
    if use_cfg is None:
        try:
            from gptreg.config import load_config
            use_cfg = load_config()
        except Exception:
            use_cfg = {}
    if (use_cfg.get("mail") or {}).get("use_xdauv", True):
        return XDAuvMailClient(account, proxy=proxy, impersonate=impersonate, cfg=use_cfg)
    # ms_oauth 走 IMAP(XOAUTH2)，绕开 Graph ~150s 索引延迟(实测稳定 0.6s)
    return IMAPOAuthClient(account, proxy=proxy, impersonate=impersonate, cfg=use_cfg)

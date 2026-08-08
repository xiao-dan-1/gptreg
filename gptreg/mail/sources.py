"""邮箱来源插件 + 注册表。

统一框架: 每个号源 = 一个 MailSource 插件(解析号池行 + 选收码客户端)。
新增号源(如 CloudMail) = 写一个类继承 MailSource + 注册进 MAIL_SOURCES,
核心(parse_mail_line / build_mail_client)零改动。

MAIL_CLIENTS 是收码通道注册表(MailClient 插件), MAIL_SOURCES 是来源注册表
(MailSource 插件)——来源决定"号池行是什么类型", 收码决定"怎么拉验证码"。
"""
from __future__ import annotations

from typing import Any

from gptreg.mail.api import ApiMailSource
from gptreg.mail.base import MailClient, MailSource
from gptreg.mail.cloudmail import CloudMailSource
from gptreg.mail.icloud_xdauv import ICloudApiClient, XDAuvMailClient
from gptreg.mail.imap import IMAPOAuthClient


class MsOAuthSource(MailSource):
    """Outlook OAuth 号源: email----password----client_id----refresh_token。

    收码: use_xdauv=true 走海外服务(XDAuv), false 走本地 IMAP(默认)。
    """

    name = "ms_oauth"

    def parse_line(self, raw: str) -> dict[str, Any] | None:
        parts = raw.split("----")
        if len(parts) < 4:
            return None
        email, password, client_id, refresh_token = (p.strip() for p in parts[:4])
        if not email or "@" not in email or not refresh_token:
            return None
        return {
            "email": email,
            "password": password,
            "client_id": client_id,
            "refresh_token": refresh_token.rstrip("$").rstrip(),
            "mail_type": "ms_oauth",
            "raw_line": raw,
        }

    def build_client(
        self, account: dict, *, proxy: str | None = None,
        impersonate: str = "chrome142", cfg: dict | None = None,
    ) -> MailClient:
        use_cfg = cfg
        if use_cfg is None:
            try:
                from gptreg.config import load_config
                use_cfg = load_config()
            except Exception:
                use_cfg = {}
        if (use_cfg.get("mail") or {}).get("use_xdauv", True):
            return MAIL_CLIENTS["ms_oauth_xdauv"](account, proxy=proxy, impersonate=impersonate, cfg=use_cfg)
        return MAIL_CLIENTS["ms_oauth"](account, proxy=proxy, impersonate=impersonate, cfg=use_cfg)


class ICloudSource(MailSource):
    """iCloud 接码号源: email----https://...code-url。

    限定 iCloud 域名(@icloud.com/@me.com), 与 api(2 段非 URL) 区分。
    收码走 GET code_url 拉码(ICloudApiClient), 不绑定 Gmail 语义。
    """

    name = "icloud"
    _DOMAINS = ("icloud.com", "me.com")

    def parse_line(self, raw: str) -> dict[str, Any] | None:
        parts = raw.split("----")
        if len(parts) != 2:
            return None
        email, code_url = parts[0].strip(), parts[1].strip()
        if not email or "@" not in email:
            return None
        if not code_url.startswith(("http://", "https://")):
            return None
        dom = email.rsplit("@", 1)[1].lower()
        if not any(dom == d or dom.endswith("." + d) for d in self._DOMAINS):
            return None
        return {
            "email": email,
            "code_url": code_url,
            "mail_type": "icloud",
            "raw_line": raw,
        }

    def build_client(
        self, account: dict, *, proxy: str | None = None,
        impersonate: str = "chrome142", cfg: dict | None = None,
    ) -> MailClient:
        return MAIL_CLIENTS["icloud"](account, proxy=proxy, impersonate=impersonate)


# ── 收码通道注册表(MailClient 插件) ─────────────────────────────
MAIL_CLIENTS: dict[str, type[MailClient]] = {
    "icloud": ICloudApiClient,  # iCloud 接码 URL(GET code_url 拉码)
    "ms_oauth": IMAPOAuthClient,  # 本地 IMAP(use_xdauv=false)
    "ms_oauth_xdauv": XDAuvMailClient,  # 服务收码(use_xdauv=true)
}

# ── 邮箱来源注册表(MailSource 插件; 新增号源在此加一项) ─────────
MAIL_SOURCES: dict[str, MailSource] = {
    "ms_oauth": MsOAuthSource(),
    "icloud": ICloudSource(),
    "api": ApiMailSource(),  # 通用第三方 API 接码(配置 mail.api_client)
    "cloudmail": CloudMailSource(),  # 自托管 cloud-mail(配置 mail.cloud_mail)
}

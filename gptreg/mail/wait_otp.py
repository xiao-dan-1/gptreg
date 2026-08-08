"""共享收码子步骤（register_pwd / register_otp 复用）。

从两条注册路径收码段抽取——消除重复:
  build_mail_client(代理决策) + UsedCodeCache + wait_for_otp + 超时重发 + 到件延迟。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from gptreg.config import _root, resolve_path
from gptreg.mail.mail_util import MailClientError, UsedCodeCache, mail_identity_key
from gptreg.mail.providers import build_mail_client

logger = logging.getLogger(__name__)


def _otp_proxy(mail_type: str, proxy_url: str | None) -> str | None:
    """收码代理决策: 仅 ms_oauth(Outlook IMAP/Graph)需走链式隧道。

    iCloud 接码 URL/CloudMail admin/API 是第三方或自托管服务, 直连可通,
    套隧道反而 TLS WRONG_VERSION_NUMBER 失败(实测: icloud-api.top 直连 200, 走代理 35 错误)。
    """
    return proxy_url if mail_type == "ms_oauth" else None


def _resolve_used_cache(cfg: dict[str, Any]) -> UsedCodeCache:
    cache_path = resolve_path(cfg.get("mail", {}).get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
    return UsedCodeCache(cache_path)


def wait_otp_with_retry(
    cfg: dict[str, Any],
    account: dict[str, Any],
    *,
    email: str,
    after_ts: float,
    proxy_url: str | None = None,
    send_url: str = "",
    session=None,
    max_attempts: int = 1,
    timeout: int = 150,
    interval: int = 3,
    settle_seconds: int = 5,
    on_poll_extra: Callable[[dict], None] | None = None,
    exclude_codes: set[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """收码(代理决策 + UsedCodeCache + wait_for_otp + 超时重发)。

    返回 (otp, extra): extra 含 otp_channel/otp_delay_s 等归因字段。
    max_attempts>1 时超时重发(register_pwd); =1 不重发(register_otp)。

    session/send_url 用于重发时重新触发 send_otp; 无重发时可为空。
    """
    mail_type = str(account.get("mail_type") or "")
    client = build_mail_client(
        account,
        proxy=_otp_proxy(mail_type, proxy_url),
        impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"),
        cfg=cfg,
    )
    # 收码通道类型(ms_oauth/icloud/cloudmail/api...), 归因分析用
    ch = mail_type or type(client).__name__.replace("Client", "").replace("OAuth", "")

    used_cache = _resolve_used_cache(cfg)
    identity = mail_identity_key(account)
    exclude = set(str(c) for c in (exclude_codes or used_cache.seen_codes(identity)))
    attempts = max(1, int(max_attempts or 1))
    otp_delay_s: float | None = None

    def _on_poll(info: dict) -> None:
        nonlocal otp_delay_s
        if info.get("elapsed_s") is not None:
            otp_delay_s = float(info["elapsed_s"])
        if on_poll_extra:
            try:
                on_poll_extra(info)
            except Exception:
                pass

    otp = None
    for attempt in range(attempts):
        try:
            otp = client.wait_for_otp(
                after_ts=after_ts, timeout=int(timeout), interval=int(interval),
                settle_seconds=settle_seconds, exclude_codes=exclude, on_poll=_on_poll,
            )
            break
        except Exception as exc:
            if attempt >= attempts - 1:
                raise MailClientError(f"OTP 收码失败: {type(exc).__name__}: {exc}") from exc
            time.sleep(1)
            if session is not None and send_url:
                from gptreg import auth
                session.get(send_url, headers=session.auth_navigate_headers(
                    referer="https://auth.openai.com/create-account/password"),
                    allow_redirects=True)
            after_ts = time.time()

    used_cache.remember(identity, otp, email=email, status="submitted")
    extra = {"otp_channel": ch}
    if otp_delay_s is not None:
        extra["otp_delay_s"] = round(otp_delay_s, 1)
    return otp, extra

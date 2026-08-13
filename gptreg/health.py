"""账号健康检查（注册后秒封检测 / 测活共用）。

从 auth.py 拆分（内聚：认证协议 vs 账号健康检查）。
"""
from __future__ import annotations

import logging
from typing import Any

from gptreg.session import BrowserSession

logger = logging.getLogger(__name__)


def _timezone_offset_min() -> int:
    """对齐 JS getTimezoneOffset（UTC+8 → -480）。"""
    try:
        from datetime import datetime

        off = datetime.now().astimezone().utcoffset()
        if off is None:
            return 0
        return -int(off.total_seconds() // 60)
    except Exception:
        return 0


def _backend_api_headers(
    session: BrowserSession,
    access_token: str,
    *,
    account_id: str = "",
    oai_session_id: str = "",
) -> dict[str, str]:
    """登录后 backend-api 头（对齐 Jennifer：Bearer + oai-device-id + account/session）。"""
    browser = session.cfg.get("browser", {}) or {}
    h = session.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = f"Bearer {access_token}"
    h["oai-device-id"] = session.device_id
    h["oai-language"] = browser.get("language", "en-US")
    if account_id:
        h["chatgpt-account-id"] = account_id
    if oai_session_id:
        h["oai-session-id"] = oai_session_id
    return h


def check_account_health_me(
    session: BrowserSession,
    access_token: str,
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    """用 /backend-api/me 做存活主判定（轻量 1.2KB，同 IP 并发不触发 WAF）。

    实测：accounts/check 同 IP 连续请求会被 WAF 403（需每 8 换 IP）；
    me 同 IP 连续测 5+ 账号全 200 无风控（社区 CLIProxyAPI 也用它批量管理）。
    返回 status: ok / deactivated / invalidated / token_expired / error。

    ⚠️ 判死边界(2026-08-13 实测): me 对"死号"统一返回 401
    {"code":"token_invalidated"}，不区分封号(account_deactivated)/删除/过期，
    故下方 deactivated/token_expired 分支是防御性(当前端点不返回这些词)，
    实际死号都落 invalidated。死因唯一可靠判别 = relogin(password/verify
    403 "account deleted" = 账号被删/封，救不回)。
    """
    if not access_token:
        return {"status": "error", "detail": "empty token", "endpoint": "me"}

    headers = session.chatgpt_headers(referer="https://chatgpt.com/")
    headers["authorization"] = f"Bearer {access_token}"
    headers["oai-device-id"] = session.device_id
    headers["oai-language"] = (session.cfg.get("browser", {}) or {}).get("language", "en-US")
    headers.pop("content-type", None)

    try:
        url = "https://chatgpt.com/backend-api/me"
        resp = session.get(url, headers=headers, timeout=timeout)
        text = (resp.text or "")[:500]
        low = text.lower()
        if resp.status_code == 200:
            return {"status": "ok", "http": 200, "endpoint": "me", "body": resp.text}
        # ⚠️ deactivated / token_expired 两分支是防御性兜底: 实测(2026-08-13) me 对死号
        # 统一返回 code=token_invalidated(不含 deactivated/deleted/token_expired 字样)，
        # 实际死号都落下方 invalidated 分支。保留以防端点未来改响应词。
        if resp.status_code in (401, 403) and (
            "account_deactivated" in low or "deactivated" in low or "deleted" in low
        ):
            return {"status": "deactivated", "http": resp.status_code, "endpoint": "me", "body": text}
        if resp.status_code == 401 and "token_expired" in low:
            return {"status": "token_expired", "http": resp.status_code, "endpoint": "me", "body": text}
        if resp.status_code in (401, 403) and (
            "token_invalidated" in low or "token_revoked" in low or "unauthorized" in low or "invalid" in low
        ):
            # invalidated = token 失效, 死因未知: 可能是账号被删/封(救不回), 也可能是
            # token 被吊销但账号仍活(relogin 可救)。轻量端点无法区分, 需 relogin 定论。
            return {"status": "invalidated", "http": resp.status_code, "endpoint": "me", "body": text}
        return {"status": "error", "http": resp.status_code, "endpoint": "me", "body": text}
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "endpoint": "me"}


def check_account_health(
    session: BrowserSession,
    access_token: str,
    *,
    timeout: float | None = None,
    prefer_me: bool = True,
) -> dict[str, Any]:
    """账号健康检查（注册后秒封检测 / 测活共用）。

    返回 status: ok / deactivated / invalidated / token_expired / error。
    默认走 /backend-api/me 快判（轻量、不风控，同 IP 并发安全）。
    ⚠️ 判死边界(2026-08-13 实测): me 与 accounts/check 对死号统一返回 401
    code=token_invalidated，不区分封号(account_deactivated)/删除/过期——
    deactivated/token_expired 分支是防御性(当前端点不返回这些词)，死号都落
    invalidated。invalidated 语义 = "token 失效, 死因未知(账号被删/封 OR token
    吊销但账号活)"，唯一可靠判别是 relogin(password/verify)。
    prefer_me=False：用 accounts/check（同 IP 连续请求会 WAF 403）。
    timeout=None 用 session 默认(60s)；测活场景建议传 ~10s。
    """
    if not access_token:
        return {"status": "error", "detail": "empty token"}
    if prefer_me:
        return check_account_health_me(session, access_token, timeout=timeout)
    return _check_accounts_check(session, access_token, timeout=timeout)


def _check_accounts_check(
    session: BrowserSession,
    access_token: str,
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    """accounts/check 兜底（秒封检测精度更高，但同 IP 连续请求会 WAF 403）。

    ⚠️ 实测(2026-08-13): accounts/check 与 me 一样，对死号统一返回 401
    code=token_invalidated(不返回 account_deactivated/token_expired 字样)，
    故 deactivated/token_expired 分支同 me 一样是防御性兜底，实际死号落 invalidated。
    """
    if not access_token:
        return {"status": "error", "detail": "empty token"}

    headers = session.chatgpt_headers(referer="https://chatgpt.com/")
    headers["authorization"] = f"Bearer {access_token}"
    headers["oai-device-id"] = session.device_id
    headers["oai-language"] = (session.cfg.get("browser", {}) or {}).get("language", "en-US")
    headers.pop("content-type", None)

    try:
        url = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
        resp = session.get(url, headers=headers, timeout=timeout)
        resp_text = resp.text or ""
        text = resp_text[:500]  # 截断仅用于关键词匹配(account_deactivated 等在响应开头)
        low = text.lower()
        if resp.status_code == 200:
            logger.info("health check OK accounts/check=200")
            # body 返回完整响应(8KB), 供 promo_data 等字段解析; 调用方按需截断
            return {"status": "ok", "http": 200, "endpoint": "accounts/check", "body": resp_text}
        if resp.status_code in (401, 403) and (
            "account_deactivated" in low or "deactivated" in low or "deleted" in low
        ):
            logger.error("health: account_deactivated http=%s", resp.status_code)
            return {
                "status": "deactivated",
                "http": resp.status_code,
                "endpoint": "accounts/check",
                "body": text,
            }
        if resp.status_code == 401 and "token_expired" in low:
            # access_token 过期(实测 ~6h, 非 README 10 天)——账号存活, session_token 可续期
            logger.warning("health: token_expired http=401 (access_token 过期, 可续期)")
            return {
                "status": "token_expired",
                "http": resp.status_code,
                "endpoint": "accounts/check",
                "body": text,
            }
        if resp.status_code == 401 and (
            "token_invalidated" in low
            or "token_revoked" in low
            or "unauthorized" in low
            or "invalid" in low
        ):
            logger.error("health: token_invalidated http=%s", resp.status_code)
            return {
                "status": "invalidated",
                "http": resp.status_code,
                "endpoint": "accounts/check",
                "body": text,
            }
        logger.warning("health unexpected http=%s body=%s", resp.status_code, text[:200])
        return {
            "status": "error",
            "http": resp.status_code,
            "endpoint": "accounts/check",
            "body": text,
        }
    except Exception as exc:
        logger.warning("health check 异常: %s", exc)
        return {"status": "error", "detail": str(exc)}

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


def check_account_health(session: BrowserSession, access_token: str) -> dict[str, Any]:
    """注册后即时健康检查。返回 status: ok / deactivated / invalidated / error。"""
    if not access_token:
        return {"status": "error", "detail": "empty token"}

    headers = session.chatgpt_headers(referer="https://chatgpt.com/")
    headers["authorization"] = f"Bearer {access_token}"
    headers["oai-device-id"] = session.device_id
    headers["oai-language"] = (session.cfg.get("browser", {}) or {}).get("language", "en-US")
    headers.pop("content-type", None)

    try:
        url = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
        resp = session.get(url, headers=headers)
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

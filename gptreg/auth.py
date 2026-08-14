"""ChatGPT / OpenAI 注册协议请求。

内聚：仅认证协议 + sentinel 分发。健康检查在 gptreg/health.py, 登录后行为在 postlogin.py。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

from gptreg.session import BrowserSession

logger = logging.getLogger(__name__)


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    # 403 也纳入可重试: 403 可能是出口 IP 临时风控(换 IP 能救), 实测 ~50% 的 403 换 IP 后成功
    keys = ("ssl", "timeout", "connection", "proxy", "curl", "reset", "refused", "403")
    return any(k in name for k in keys) or any(k in msg for k in keys)


def get_providers(session: BrowserSession) -> dict:
    url = "https://chatgpt.com/api/auth/providers"
    logger.info("[Auth] 获取 providers")
    resp = session.get(url, headers=session.chatgpt_headers())
    resp.raise_for_status()
    return resp.json()


def get_csrf_token(session: BrowserSession) -> str:
    url = "https://chatgpt.com/api/auth/csrf"
    logger.info("[Auth] 获取 CSRF")
    resp = session.get(url, headers=session.chatgpt_headers())
    resp.raise_for_status()
    token = resp.json().get("csrfToken", "")
    if not token:
        raise RuntimeError("csrfToken 为空")
    return token


def signin_openai(session: BrowserSession, csrf_token: str, email: str) -> str:
    query = {
        "prompt": "login",
        "ext-oai-did": session.device_id,
        "auth_session_logging_id": session.auth_session_logging_id,
        "ext-passkey-client-capabilities": "11111",
        "screen_hint": "login_or_signup",
        "login_hint": email,
    }
    url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(query)
    headers = session.chatgpt_headers()
    headers["content-type"] = "application/x-www-form-urlencoded"
    headers["origin"] = "https://chatgpt.com"
    body = urlencode(
        {
            "callbackUrl": "/",
            "csrfToken": csrf_token,
            "json": "true",
        }
    )
    logger.info("[Auth] signin openai, email=%s", email)
    resp = session.post(url, headers=headers, data=body)
    resp.raise_for_status()
    authorize_url = resp.json().get("url", "")
    if not authorize_url:
        raise RuntimeError(f"signin 未返回 authorize url: {resp.text[:300]}")
    return authorize_url


def signin_flow(
    session: BrowserSession,
    email: str,
    *,
    follow_sleep: float = 0.4,
    authorize_attempts: int = 1,
) -> str:
    """统一 signin 序列: get_providers → CSRF → signin → authorize。

    协议步骤间的节奏(sleep)内聚在此, 调用方(register_pwd/register_otp)不再散落
    time.sleep——消除两条注册路径对协议时序的重复硬编码。
    返回 authorize 落点 URL。
    """
    get_providers(session)
    time.sleep(0.2)
    csrf = get_csrf_token(session)
    time.sleep(0.2)
    authorize_url = signin_openai(session, csrf, email)
    time.sleep(0.2)
    final = follow_authorize(session, authorize_url, attempts=authorize_attempts)
    time.sleep(follow_sleep)
    return final


def follow_authorize(session: BrowserSession, authorize_url: str, attempts: int = 3) -> str:
    headers = session.auth_navigate_headers(referer="https://chatgpt.com/")
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            logger.info("[Auth] follow authorize (%s/%s)", i, attempts)
            resp = session.get(authorize_url, headers=headers, allow_redirects=True)
            resp.raise_for_status()
            logger.info("[Auth] authorize 落点: %s", resp.url)
            return resp.url
        except Exception as exc:
            last_exc = exc
            if not _is_transient(exc) or i >= attempts:
                raise
            time.sleep(2 ** (i - 1))
    raise last_exc or RuntimeError("follow authorize 失败")


def _sentinel_source(session: BrowserSession, source: str | None = None) -> str:
    """pow | browser（真页真 t）| node（假 t）| quickjs（Node VM 真 t，突破）。"""
    raw = (source if source is not None else (session.cfg.get("protocol") or {}).get("sentinel_source") or "pow")
    mode = str(raw or "pow").strip().lower()
    if mode in {"browser", "pw", "playwright", "chrome"}:
        return "browser"
    if mode in {"node", "node_vm", "nodepow"}:
        return "node"
    if mode in {"quickjs", "qjs"}:
        return "quickjs"
    if mode in {"browser_t_quickjs_so", "bt_vs", "btqs", "true_t_vm_so"}:
        return "browser_t_quickjs_so"
    if mode in {"quickjs_t_browser_so", "qt_bs", "qtbs", "vm_t_true_so"}:
        return "quickjs_t_browser_so"
    if mode in {"quickjs_pwd_v3", "pwd", "pwd_v3", "qt_pwd"}:
        return "quickjs_pwd_v3"
    return "pow"


def make_sentinel_headers(
    session: BrowserSession,
    challenge: dict | None,
    flow: str,
    *,
    require_so: bool | None = None,
    source: str | None = None,
) -> tuple[str, str | None]:
    """生成 sentinel-token + 可选 so-token。

    按 source 查 sentinel_engine 注册表调用对应引擎(新增引擎不改本函数,开闭原则)。
    source: pow | browser | quickjs | quickjs_pwd_v3 | browser_t_quickjs_so | quickjs_t_browser_so | node
    """
    mode = _sentinel_source(session, source)
    from gptreg.sentinel_engine import get_engine

    result = get_engine(mode).generate(session, flow, session.cfg)
    token, so = result.token, result.so
    t_len = result.meta.get("t_len") or 0
    if not t_len:
        try:
            tj = json.loads(token)
            t_len = len(str(tj.get("t") or ""))
        except Exception:
            t_len = 0
    session._last_sentinel_meta = {  # type: ignore[attr-defined]
        "mode": mode,
        "has_so": bool(so),
        "so_len": len(so or ""),
        "t_len": t_len,
        **result.meta,
    }
    if mode == "pow":
        chatreq_obs = result.meta.get("chatreq") or {}
        session._last_chatreq_obs = chatreq_obs  # type: ignore[attr-defined]
        if chatreq_obs.get("so_required") and not so:
            logger.warning(
                "[Sentinel] chatReq 要求 so 但 pow 路径无 so flow=%s collector_dx_len=%s pow_so_source=%s",
                flow, chatreq_obs.get("so_collector_dx_len"), result.meta.get("pow_so_source"),
            )
    logger.info(
        "[Sentinel] headers ready flow=%s mode=%s has_so=%s so_len=%s t_len=%s",
        flow, mode, bool(so), len(so or ""), t_len,
    )
    return token, so


def validate_email_otp(session: BrowserSession, code: str, sentinel_header: str | None = None) -> dict:
    url = "https://auth.openai.com/api/accounts/email-otp/validate"
    headers = session.auth_api_headers(referer="https://auth.openai.com/email-verification")
    if sentinel_header:
        headers["openai-sentinel-token"] = sentinel_header
    body = json.dumps({"code": code})
    logger.info("[Auth] validate OTP")
    resp = session.post(url, headers=headers, data=body)
    if resp.status_code != 200:
        logger.error("[Auth] OTP 失败 %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
    data = resp.json()
    page = (data.get("page") or {}).get("type") or data.get("type")
    logger.info("[Auth] OTP 通过 page=%s", page)
    return data


def warm_about_you(session: BrowserSession, referer: str = "https://auth.openai.com/email-verification") -> str:
    """OTP 通过后访问 about-you，推进 session 状态（k12：防 invalid_auth_step）。"""
    url = "https://auth.openai.com/about-you"
    headers = session.auth_navigate_headers(referer=referer)
    headers["sec-fetch-site"] = "same-origin"
    logger.info("[Auth] warm about-you")
    try:
        resp = session.get(url, headers=headers, allow_redirects=True)
        logger.info("[Auth] about-you 落点: %s status=%s", getattr(resp, "url", url), resp.status_code)
        return getattr(resp, "url", url) or url
    except Exception as exc:
        logger.warning("[Auth] warm about-you 失败: %s", exc)
        return url


def create_account(
    session: BrowserSession,
    name: str,
    birthdate: str,
    sentinel_header: str,
    so_header: str | None = None,
    *,
    require_so: bool = False,
) -> dict:
    url = "https://auth.openai.com/api/accounts/create_account"
    headers = session.auth_api_headers(referer="https://auth.openai.com/about-you")
    headers["openai-sentinel-token"] = sentinel_header
    if so_header:
        headers["openai-sentinel-so-token"] = so_header
    elif require_so:
        logger.warning("[Auth] create_account 期望 so_header 但未提供，继续（对齐参考实现）")
    body = json.dumps({"name": name, "birthdate": birthdate})
    logger.info(
        "[Auth] create_account name=%s birthdate=%s has_so=%s",
        name,
        birthdate,
        bool(so_header),
    )
    resp = session.post(url, headers=headers, data=body)
    # k12: session 未推进到 about-you 时偶发 invalid_auth_step → warm 后重试一次
    if resp.status_code == 400 and "invalid_auth_step" in (resp.text or ""):
        logger.warning("[Auth] create_account invalid_auth_step，warm 后重试")
        warm_about_you(session)
        try:
            cb_headers = session.auth_navigate_headers(referer="https://auth.openai.com/email-verification")
            cb_headers["sec-fetch-site"] = "same-origin"
            session.get(
                "https://auth.openai.com/api/accounts/authorize/callback",
                headers=cb_headers,
                allow_redirects=True,
            )
        except Exception:
            pass
        resp = session.post(url, headers=headers, data=body)
    if resp.status_code != 200:
        body = (resp.text or "")[:400]
        logger.error("[Auth] create_account 失败 %s: %s", resp.status_code, body)
        # 把 body/code 带进异常，便于 pipeline 失败分桶（raise_for_status 会丢掉正文）
        raise RuntimeError(f"create_account HTTP {resp.status_code}: {body}")
    return resp.json()


def follow_oauth_callback(session: BrowserSession, continue_url: str) -> str:
    if not continue_url:
        raise ValueError("continue_url 为空")
    headers = session.auth_navigate_headers(referer="https://auth.openai.com/about-you")
    headers["sec-fetch-site"] = "same-origin"
    logger.info("[Auth] follow OAuth callback")
    resp = session.get(continue_url, headers=headers, allow_redirects=True)
    logger.info("[Auth] callback 落点: %s", resp.url)
    return resp.url


def fetch_session(session: BrowserSession) -> dict:
    url = "https://chatgpt.com/api/auth/session"
    headers = session.chatgpt_headers(referer="https://chatgpt.com/")
    headers.pop("content-type", None)
    logger.info("[Auth] fetch session")
    resp = session.get(url, headers=headers)
    if resp.status_code != 200:
        logger.error("[Auth] session 失败 %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
    data = resp.json()
    if not data.get("accessToken"):
        raise RuntimeError(f"session 无 accessToken: {str(data)[:200]}")
    return data


def maybe_follow_external(session: BrowserSession, validate_result: dict) -> None:
    page_type = (validate_result.get("page") or {}).get("type") or validate_result.get("type")
    if page_type != "external_url":
        return
    external = validate_result.get("continue_url") or ""
    if not external:
        for key in ("url", "redirect_url", "externalUrl"):
            val = validate_result.get(key)
            if isinstance(val, str) and val.startswith("http"):
                external = val
                break
    if external:
        logger.info("[Auth] 跟随 external_url")
        session.get(external, headers=session.auth_navigate_headers(), allow_redirects=True)


# 兼容转发: 健康检查/登录后行为已拆分到 health/postlogin(旧脚本从 auth import 仍可用)
from gptreg.health import check_account_health  # noqa: E402
from gptreg.postlogin import post_login_warmup  # noqa: E402

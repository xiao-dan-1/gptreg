"""ChatGPT / OpenAI 注册协议请求。"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

from gptreg.sentinel import (
    SentinelPoW,
    build_sentinel_request_body,
    build_so_header,
    resolve_pow_so_header,
    generate_requirements_token,
)
from gptreg.session import BrowserSession

logger = logging.getLogger(__name__)


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    keys = ("ssl", "timeout", "connection", "proxy", "curl", "reset", "refused")
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
        "ext-passkey-client-capabilities": "1111",
        "screen_hint": "login_or_signup",
        "login_hint": email,
    }
    url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(query)
    headers = session.chatgpt_headers()
    headers["content-type"] = "application/x-www-form-urlencoded"
    headers["origin"] = "https://chatgpt.com"
    body = urlencode(
        {
            "callbackUrl": "https://chatgpt.com/",
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


def request_sentinel(session: BrowserSession, flow: str) -> dict:
    from gptreg.sentinel import log_chatreq_obs

    p = generate_requirements_token(session.cfg, session.device_id)
    body = build_sentinel_request_body(p, session.device_id, flow)
    url = "https://sentinel.openai.com/backend-api/sentinel/req"
    logger.info("[Sentinel] req flow=%s", flow)
    resp = session.post(url, headers=session.sentinel_headers(), data=body)
    resp.raise_for_status()
    data = resp.json()
    # 诊断探针：chatReq.so / collector_dx（不产 so、不改 token）
    session._last_chatreq_obs = log_chatreq_obs(  # type: ignore[attr-defined]
        data, flow=flow, http=getattr(resp, "status_code", None)
    )
    return data


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

    source:
      - pow（默认）: 纯 Python FNV-1a，t=\"\"，通常无 so
      - browser: 真 Chrome token() + sessionObserverToken()（opt-in）
    假 so（SyntaxError / MDogU3ludGF4）一律丢弃，禁止伪造。
    P1：本环境 create 有/无 so ≥2h 双活，默认保持 pow。
    """
    mode = _sentinel_source(session, source)

    if mode == "browser":
        from gptreg.browser_sentinel import harvest_for_session

        token, so, meta = harvest_for_session(session, flow)
        # 再滤一遍假 so
        if so and ("SyntaxError" in so or "MDogU3ludGF4" in so):
            logger.warning("[Sentinel] browser so-header 假值，丢弃 flow=%s", flow)
            so = None
        logger.info(
            "[Sentinel] headers ready flow=%s mode=browser has_so=%s so_len=%s t_len=%s elapsed=%s",
            flow,
            bool(so),
            len(so or ""),
            meta.get("t_len"),
            meta.get("elapsed_s"),
        )
        # 挂观测，供 pipeline 读取
        session._last_sentinel_meta = {  # type: ignore[attr-defined]
            "mode": "browser",
            "has_so": bool(so),
            "so_len": len(so or ""),
            "t_len": meta.get("t_len"),
            "sdk_keys": meta.get("sdk_keys"),
            "elapsed_s": meta.get("elapsed_s"),
            "so_api_err": meta.get("so_api_err"),
        }
        return token, so

    if mode == "quickjs":
        # Node VM 跑官方 sdk.js 产**真 t + 真 so**（协议产真 t/so 突破，capture/protocol-real-t-20260803/）。
        # 无浏览器；每 token ~60s（t 解释器慢，so 快）。
        from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs

        token, so = get_sentinel_token_via_quickjs(
            session, session.device_id, flow=flow, cfg=session.cfg,
        )
        try:
            tj = json.loads(token)
            _t_len = len(str(tj.get("t") or ""))
        except Exception:
            _t_len = 0
        _so_len = len(so or "")
        session._last_sentinel_meta = {  # type: ignore[attr-defined]
            "mode": "quickjs",
            "has_so": bool(so),
            "so_len": _so_len,
            "t_len": _t_len,
        }
        logger.info(
            "[Sentinel] headers ready flow=%s mode=quickjs t_len=%s so_len=%s (真 t+真 so)",
            flow, _t_len, _so_len,
        )
        return token, so

    if mode == "node":
        # 默认 create 路线（2026-08 起）：Node VM 跑 sdk.js 产 token（t 非空即过 create）。
        # 无浏览器、快；t 为假值但非空即可。参考 turb-gpt-free-register protocol 驱动。
        from gptreg.sentinel import generate_sentinel_token_via_node

        challenge = request_sentinel(session, flow)
        token = generate_sentinel_token_via_node(
            session.cfg, challenge, flow, session.device_id,
            user_agent=session.user_agent,
        )
        # 丢弃假 so（node runner 无 sessionObserverToken，通常无）
        try:
            parsed = json.loads(token)
            so_val = parsed.get("so")
            if isinstance(so_val, str) and (
                "SyntaxError" in so_val or so_val.startswith("MDogU3ludGF4")
            ):
                parsed.pop("so", None)
                token = json.dumps(parsed, separators=(",", ":"))
        except Exception:
            pass
        so = build_so_header(token, session.device_id, flow)
        try:
            tj = json.loads(token)
            _t_len = len(str(tj.get("t") or ""))
        except Exception:
            _t_len = 0
        session._last_sentinel_meta = {  # type: ignore[attr-defined]
            "mode": "node",
            "has_so": bool(so),
            "so_len": len(so or ""),
            "t_len": _t_len,
        }
        logger.info(
            "[Sentinel] headers ready flow=%s mode=node has_so=%s so_len=%s t_len=%s",
            flow, bool(so), len(so or ""), _t_len,
        )
        return token, so

    browser_cfg = session.cfg.get("browser") or {}
    pow_engine = SentinelPoW(
        ua=session.user_agent,
        sv=getattr(session, "sentinel_sv", "") or "",
        device_id=session.device_id,
        cores=browser_cfg.get("hardware_concurrency"),
        screen_w=int(browser_cfg.get("screen_width") or 1920),
        screen_h=int(browser_cfg.get("screen_height") or 1080),
    )
    token = pow_engine.build(session.session, session.device_id, flow)
    chatreq_obs = getattr(pow_engine, "last_chatreq_obs", None) or {}

    # 丢弃假 so
    try:
        parsed = json.loads(token)
        so_val = parsed.get("so")
        if isinstance(so_val, str) and ("SyntaxError" in so_val or so_val.startswith("MDogU3ludGF4")):
            parsed.pop("so", None)
            token = json.dumps(parsed, separators=(",", ":"))
            logger.warning("[Sentinel] 丢弃假 so（SyntaxError）flow=%s", flow)
        # starmiaoa build_sentinel_token 返回 oai_sc=0+c；写 cookie 对齐（registrar 未必用）
        c_tok = str(parsed.get("c") or "").strip()
        if c_tok and hasattr(session, "set_oai_sc"):
            session.set_oai_sc(c_tok)
    except Exception:
        pass

    proto = session.cfg.get("protocol") or {}
    pow_so_source = str(proto.get("pow_so_source") or "none").strip().lower()
    so = resolve_pow_so_header(
        token, session.device_id, flow, pow_so_source=pow_so_source
    )
    # 仅丢 SyntaxError/jsdom 假 so；小PP HAR so 放行（pow_so_source=xiaopp）
    if so and ("SyntaxError" in so or "MDogU3ludGF4" in so):
        logger.warning("[Sentinel] so-header 含 SyntaxError，丢弃")
        so = None

    if chatreq_obs.get("so_required") and not so:
        logger.warning(
            "[Sentinel] chatReq 要求 so 但 pow 路径无 so flow=%s collector_dx_len=%s pow_so_source=%s",
            flow,
            chatreq_obs.get("so_collector_dx_len"),
            pow_so_source,
        )

    logger.info(
        "[Sentinel] headers ready flow=%s mode=pow has_so=%s so_len=%s so_required=%s pow_so_source=%s collector_dx_len=%s",
        flow,
        bool(so),
        len(so or ""),
        chatreq_obs.get("so_required"),
        pow_so_source,
        chatreq_obs.get("so_collector_dx_len"),
    )
    session._last_sentinel_meta = {  # type: ignore[attr-defined]
        "mode": "pow",
        "has_so": bool(so),
        "so_len": len(so or ""),
        "pow_so_source": pow_so_source,
        "chatreq": chatreq_obs,
    }
    session._last_chatreq_obs = chatreq_obs  # type: ignore[attr-defined]
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
        text = (resp.text or "")[:500]
        low = text.lower()
        if resp.status_code == 200:
            logger.info("[Auth] health check OK accounts/check=200")
            return {"status": "ok", "http": 200, "endpoint": "accounts/check", "body": text}
        if resp.status_code in (401, 403) and (
            "account_deactivated" in low or "deactivated" in low or "deleted" in low
        ):
            logger.error("[Auth] health: account_deactivated http=%s", resp.status_code)
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
            logger.error("[Auth] health: token_invalidated http=%s", resp.status_code)
            return {
                "status": "invalidated",
                "http": resp.status_code,
                "endpoint": "accounts/check",
                "body": text,
            }
        logger.warning("[Auth] health unexpected http=%s body=%s", resp.status_code, text[:200])
        return {
            "status": "error",
            "http": resp.status_code,
            "endpoint": "accounts/check",
            "body": text,
        }
    except Exception as exc:
        logger.warning("[Auth] health check 异常: %s", exc)
        return {"status": "error", "detail": str(exc)}


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


def post_login_warmup(
    session: BrowserSession,
    access_token: str,
    session_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Step B：登录后最小补齐（so 策略不变，不造假 PoW/turnstile/finalize）。

    对照：
    - Jennifer capture：me/check → conversation/init → chat-requirements prepare/finalize
    - 协议分析：init 不需对话 sentinel token；prepare→finalize 需真 PoW/turnstile

    本实现只保证能诚实完成的部分：
    1) GET /backend-api/me
    2) POST /backend-api/conversation/init
    3) POST chat-requirements/prepare（带本机 generate 的 p；失败仅记录）
    4) finalize **跳过**（无真 pow/turnstile 解，禁止伪造）
    """
    import uuid

    info = session_info or {}
    account = info.get("account") or {}
    account_id = ""
    if isinstance(account, dict):
        account_id = str(account.get("id") or "")
    oai_session_id = str(uuid.uuid4())
    detail: dict[str, Any] = {
        "enabled": True,
        "account_id": account_id or None,
        "oai_session_id": oai_session_id,
        "steps": {},
        "finalize": "skipped_no_real_pow_turnstile",
    }

    def _step(name: str, fn) -> None:
        try:
            detail["steps"][name] = fn()
        except Exception as exc:
            logger.warning("[PostLogin] %s 异常: %s", name, exc)
            detail["steps"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # 1) /me — Jennifer 登录后立刻打
    def _me() -> dict[str, Any]:
        headers = _backend_api_headers(
            session, access_token, account_id=account_id, oai_session_id=oai_session_id
        )
        headers.pop("content-type", None)
        resp = session.get("https://chatgpt.com/backend-api/me", headers=headers)
        body = (resp.text or "")[:200]
        ok = resp.status_code == 200
        logger.info("[PostLogin] me http=%s ok=%s", resp.status_code, ok)
        return {"ok": ok, "http": resp.status_code, "body_head": body}

    # 2) conversation/init — 开壳，不需对话 sentinel token
    def _init() -> dict[str, Any]:
        headers = _backend_api_headers(
            session, access_token, account_id=account_id, oai_session_id=oai_session_id
        )
        payload = {
            "requested_default_model": None,
            "conversation_id": None,
            "timezone_offset_min": _timezone_offset_min(),
        }
        logger.info(
            "[PostLogin] conversation/init tz_offset=%s account_id=%s",
            payload["timezone_offset_min"],
            account_id[:8] if account_id else "-",
        )
        resp = session.post(
            "https://chatgpt.com/backend-api/conversation/init",
            headers=headers,
            json=payload,
        )
        body = (resp.text or "")[:240]
        ok = resp.status_code == 200
        logger.info("[PostLogin] conversation/init http=%s ok=%s", resp.status_code, ok)
        return {"ok": ok, "http": resp.status_code, "body_head": body}

    # 3) prepare — 尽力；不解 finalize
    def _prepare() -> dict[str, Any]:
        from gptreg.sentinel import summarize_chatreq

        headers = _backend_api_headers(
            session, access_token, account_id=account_id, oai_session_id=oai_session_id
        )
        p = generate_requirements_token(session.cfg, session.device_id)
        logger.info("[PostLogin] chat-requirements/prepare p_len=%s", len(p or ""))
        resp = session.post(
            "https://chatgpt.com/backend-api/sentinel/chat-requirements/prepare",
            headers=headers,
            json={"p": p},
        )
        text = resp.text or ""
        body_head = text[:240]
        out: dict[str, Any] = {
            "ok": resp.status_code == 200,
            "http": resp.status_code,
            "body_head": body_head,
            "p_len": len(p or ""),
        }
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                data = {}
            if isinstance(data, dict):
                out["has_prepare_token"] = bool(data.get("prepare_token"))
                out["prepare_token_len"] = len(str(data.get("prepare_token") or ""))
                out["persona"] = data.get("persona")
                out["resp_keys"] = sorted(data.keys())
                pow_req = data.get("proofofwork") or {}
                ts_req = data.get("turnstile") or {}
                so_req = data.get("so") or {}
                out["pow_required"] = bool(pow_req.get("required")) if isinstance(pow_req, dict) else None
                out["turnstile_required"] = bool(ts_req.get("required")) if isinstance(ts_req, dict) else None
                out["so_required"] = bool(so_req.get("required")) if isinstance(so_req, dict) else None
                if isinstance(so_req, dict):
                    out["so_keys"] = sorted(so_req.keys())
                    cdx = so_req.get("collector_dx")
                    sdx = so_req.get("snapshot_dx")
                    out["so_collector_dx_len"] = len(cdx) if isinstance(cdx, str) else 0
                    out["so_snapshot_dx_len"] = len(sdx) if isinstance(sdx, str) else 0
                if isinstance(ts_req, dict):
                    dx = ts_req.get("dx")
                    out["turnstile_dx_len"] = len(dx) if isinstance(dx, str) else 0
                if isinstance(pow_req, dict):
                    out["pow_difficulty"] = str(pow_req.get("difficulty") or "")
                    out["pow_seed_len"] = len(str(pow_req.get("seed") or ""))
                # 与 auth /req chatReq 观测同形（诊断 only）
                out["chatreq"] = summarize_chatreq(
                    data, flow="chat_requirements_prepare", http=resp.status_code
                )
                # 明确不 finalize：无真解
                out["finalize"] = "skipped_no_real_pow_turnstile"
        logger.info(
            "[PostLogin] prepare http=%s ok=%s has_prepare_token=%s so_required=%s "
            "collector_dx_len=%s snapshot_dx_len=%s turnstile_dx_len=%s",
            resp.status_code,
            out.get("ok"),
            out.get("has_prepare_token"),
            out.get("so_required"),
            out.get("so_collector_dx_len"),
            out.get("so_snapshot_dx_len"),
            out.get("turnstile_dx_len"),
        )
        return out

    logger.info("[PostLogin] warmup start (init+prepare only; no fake finalize/so)")
    _step("me", _me)
    time.sleep(0.2)
    _step("conversation_init", _init)
    time.sleep(0.2)
    _step("chat_requirements_prepare", _prepare)
    detail["ok"] = bool((detail["steps"].get("conversation_init") or {}).get("ok"))
    logger.info(
        "[PostLogin] warmup done ok=%s steps=%s",
        detail["ok"],
        {k: (v or {}).get("http") for k, v in detail["steps"].items()},
    )
    return detail


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
        time.sleep(1)

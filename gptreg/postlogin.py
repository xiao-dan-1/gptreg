"""登录后最小行为补齐（存活考量；不造假 PoW/turnstile/finalize）。

从 auth.py 拆分（内聚：认证协议 vs 登录后行为）。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from gptreg.health import _backend_api_headers, _timezone_offset_min
from gptreg.sentinel import generate_requirements_token
from gptreg.sentinel_chatreq import summarize_chatreq

logger = logging.getLogger(__name__)


def post_login_warmup(
    session: Any,
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
                out["chatreq"] = summarize_chatreq(
                    data, flow="chat_requirements_prepare", http=resp.status_code
                )
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

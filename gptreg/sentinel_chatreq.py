"""Sentinel /req chatReq 观测（诊断用）：只观测不产 token/so。

从 sentinel.py 拆出——sentinel.py 专注于 PoW 生成, chatReq 观测是独立的诊断职责。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def summarize_chatreq(data: dict[str, Any] | None, *, flow: str = "", http: int | None = None) -> dict[str, Any]:
    """诊断用：从 sentinel/req 响应提取 so/turnstile/pow 是否被要求。不产 so、不改 token。"""
    d = data if isinstance(data, dict) else {}
    so = d.get("so") if isinstance(d.get("so"), dict) else {}
    ts = d.get("turnstile") if isinstance(d.get("turnstile"), dict) else {}
    pow_d = d.get("proofofwork") if isinstance(d.get("proofofwork"), dict) else {}
    cdx = so.get("collector_dx") if so else None
    dx = ts.get("dx") if ts else None
    requires: list[str] = []
    if pow_d.get("required"):
        requires.append("pow")
    if ts.get("required"):
        requires.append("turnstile")
    if so.get("required"):
        requires.append("so")
    return {
        "flow": flow or None,
        "http": http,
        "keys": sorted(d.keys()) if d else [],
        "has_so_field": bool(so) or ("so" in d),
        "so_required": bool(so.get("required")) if so else False,
        "so_collector_dx_len": len(cdx) if isinstance(cdx, str) else 0,
        "so_keys": sorted(so.keys()) if so else [],
        "turnstile_required": bool(ts.get("required")) if ts else False,
        "turnstile_dx_len": len(dx) if isinstance(dx, str) else 0,
        "pow_required": bool(pow_d.get("required")) if pow_d else False,
        "pow_difficulty": str(pow_d.get("difficulty") or "") if pow_d else "",
        "pow_seed_len": len(str(pow_d.get("seed") or "")) if pow_d else 0,
        "token_c_len": len(str(d.get("token") or "")),
        "persona": d.get("persona"),
        "requires": requires,
    }


def log_chatreq_obs(data: dict[str, Any] | None, *, flow: str = "", http: int | None = None, prefix: str = "[Sentinel/chatReq]") -> dict[str, Any]:
    """打一行紧凑观测日志，返回 summary dict。"""
    s = summarize_chatreq(data, flow=flow, http=http)
    logger.info(
        "%s flow=%s http=%s keys=%s requires=%s "
        "so_field=%s so_required=%s collector_dx_len=%s so_keys=%s "
        "turnstile_required=%s dx_len=%s pow_required=%s difficulty=%s c_len=%s persona=%s",
        prefix,
        s.get("flow"),
        s.get("http"),
        s.get("keys"),
        s.get("requires") or ["none"],
        s.get("has_so_field"),
        s.get("so_required"),
        s.get("so_collector_dx_len"),
        s.get("so_keys"),
        s.get("turnstile_required"),
        s.get("turnstile_dx_len"),
        s.get("pow_required"),
        s.get("pow_difficulty"),
        s.get("token_c_len"),
        s.get("persona"),
    )
    return s



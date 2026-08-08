"""Sentinel so-token 头构造（从 sentinel.py 拆出——纯 so 头职责）。

sentiniel.py 专注 PoW 生成, so 头构造(小PP HAR / token 内嵌 so 包装)独立于此。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 神奇的小PP protocol_register/sentinel.py · 对齐 sentinel.go HAR 抓包 so（纯协议无浏览器）
# 仅当 protocol.pow_so_source=xiaopp 时用于 openai-sentinel-so-token；不是 sessionObserverToken。
_XIAOPP_HAR_SO = (
    "QhccBRcGGxQDF29nCW1vdFpxZlFgf2xkDXJtQWBib1FKCAwaGwceGAEHDAwbdm9zBBcCFAsbGAUbDwx1a0"
    "ZLd2hSbH54AWN3UQ1tdU4FTHdoUmx+eAgTFBUXGBgLFxQUenQTCxsZDAcOGxYBGw8MdUFGd3doRmNxaFZn"
    "dHtea3VeeHl0aHQTFBUXGgQXBh0UAxdtZwQIDBobDRgYCgIMDBt2fwsEFwIUAQIABwwXFBR6ZBMLGxkMAQ"
    "obHQEbDwx1eHx5dGgBbHFeVmxya0ZrcmgIExQVFx8BFw0MDBt2b39ud38Ce3JJVXh0Rll8dm8LBBcCFAsM"
    "AAEOFxQUenRZUntkFgsbGQwODBsfDhsPDHJOCBMUFRcdGAANDAwbdm9/XBcCFA8DAAEPFxQUfVJ7bH50SX"
    "BxUnd8e2cac3pSHld7Qm8LGxkMAggbGwUbDwx1XggTFBUXGAcXAxoUAxdtdEptb1F8cmZRaHxvdFJxbUF0"
    "VG93DQgMGhsMHBgABAwMG3F8RVp0f1l/cm97cHRsXXtxb0Fjd393BBcCFAwGAA8OFxQUemddV3tkGncbSA=="
)

# create / user_register 等需要 so 头的 flow（对齐小PP）
_XIAOPP_SO_FLOWS = frozenset({
    "oauth_create_account",
    "username_password_create",
})


def build_xiaopp_so_header(
    *,
    c: str,
    device_id: str,
    flow: str,
    so_value: str | None = None,
) -> str:
    """小PP openai-sentinel-so-token：{so: HAR, c, id, flow}。纯协议，无浏览器。"""
    return json.dumps(
        {
            "so": so_value or _XIAOPP_HAR_SO,
            "c": c or "",
            "id": device_id or "",
            "flow": flow or "",
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def build_so_header(
    token_json: str, device_id: str, flow: str, challenge_token: str = ""
) -> str | None:
    """从 token JSON 内嵌 so 字段包装 so-header；无 so 字段则 None。"""
    try:
        parsed = json.loads(token_json)
    except Exception:
        return None
    so_value = parsed.get("so")
    if not so_value:
        return None
    return json.dumps(
        {
            "so": so_value,
            "c": parsed.get("c") or challenge_token or "",
            "id": device_id or parsed.get("id") or "",
            "flow": flow or parsed.get("flow") or "",
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def resolve_pow_so_header(
    token_json: str,
    device_id: str,
    flow: str,
    *,
    pow_so_source: str = "none",
) -> str | None:
    """pow 路径 so-header。

    pow_so_source:
      - none: 仅 token 内嵌 so（通常无）
      - xiaopp: create 等 flow 用小PP HAR so + 本次 /req 的 c
    """
    mode = (pow_so_source or "none").strip().lower()
    if mode in {"xiaopp", "har", "har_so", "pp", "xiaopp_har"}:
        if flow not in _XIAOPP_SO_FLOWS:
            return build_so_header(token_json, device_id, flow)
        try:
            parsed = json.loads(token_json)
        except Exception:
            parsed = {}
        c = str((parsed or {}).get("c") or "").strip()
        return build_xiaopp_so_header(c=c, device_id=device_id, flow=flow)
    return build_so_header(token_json, device_id, flow)


def token_has_so(token_json: str) -> bool:
    try:
        data = json.loads(token_json)
    except Exception:
        return False
    so = data.get("so")
    return isinstance(so, str) and bool(so)


def require_so_if_needed(token_json: str, challenge: dict | None = None) -> None:
    if not challenge:
        return
    so = challenge.get("so") or {}
    if not so.get("required"):
        return
    if token_has_so(token_json):
        try:
            data = json.loads(token_json)
            so_val = data.get("so") or ""
            if isinstance(so_val, str) and (
                "SyntaxError" in so_val or so_val.startswith("MDogU3ludGF4")
            ):
                logger.warning("[Sentinel] so 字段疑似假值(SyntaxError)，将丢弃")
        except Exception:
            pass
        return
    logger.warning(
        "[Sentinel] challenge 要求 so，但 runner 未产出 so（纯 PoW 模式，预期行为）"
    )

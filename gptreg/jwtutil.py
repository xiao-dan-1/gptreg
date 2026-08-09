"""JWT 解析工具(不验签): access_token 解码 email/name/exp。

raw-check/refresh 复用(替代各自 decode_jwt/_exp_days), 统一 base64 解码。
"""
from __future__ import annotations

import base64
import json
import re
import time


def decode_jwt(token: str) -> dict:
    """解析 JWT payload(不验签), 返回 email/name/exp 或抛异常。"""
    token = re.sub(r"\s+", "", token)  # 粘贴可能被换行截断, 去掉所有空白
    seg = token.split(".")[1]
    seg += "=" * (-len(seg) % 4)
    p = json.loads(base64.urlsafe_b64decode(seg))
    prof = p.get("https://api.openai.com/profile", {}) or {}
    au = p.get("https://api.openai.com/auth", {}) or {}
    return {
        "email": prof.get("email", ""),
        "name": prof.get("name", ""),
        "exp": p.get("exp", 0),
        "acct": au.get("chatgpt_account_id", ""),
    }


def jwt_exp(token: str) -> tuple[int, int]:
    """返回 (iat, exp)。解不出返回 (0, 0)。"""
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        p = json.loads(base64.urlsafe_b64decode(seg))
        return int(p.get("iat", 0) or 0), int(p.get("exp", 0) or 0)
    except Exception:
        return 0, 0


def exp_days(token: str) -> str:
    """access_token JWT exp → 剩余天数(续期展示用, 与 refresh_at._exp_days 同口径)。"""
    _, exp = jwt_exp(token)
    if not exp:
        return "?"
    return f"{(exp - time.time())/86400:.1f}d"

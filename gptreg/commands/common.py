"""CLI 命令共享 helper: 代理参数归一 / region 覆盖 / 换 IP 轮换会话 / 年龄显示。

survival/refresh 用 RotatingSession; overview/survival 用 age_* 系列。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from gptreg.proxyutil import build_dynamic_proxy, resolve_proxy
from gptreg.session import BrowserSession


def resolve_proxy_arg(args: Any) -> str | None:
    """代理参数归一: empty/none/direct/空串 → 直连(""); None → config 逻辑。

    register/check-proxy 复用(从原 cli.py _resolve_proxy_arg 迁入)。
    """
    if getattr(args, "no_proxy", False):
        return ""
    if args.proxy is None:
        return None
    if str(args.proxy).strip().lower() in {"empty", "none", "direct", ""}:
        return ""
    return str(args.proxy).strip()


def apply_region(cfg: dict[str, Any], region: str | None) -> None:
    """--region 覆盖 config; 显式指定时自动打开动态代理(若有 template/user)。"""
    if not region:
        return
    dyn = cfg.setdefault("proxy", {}).setdefault("dynamic", {})
    dyn["region"] = region.strip().upper()
    if not dyn.get("enabled"):
        if dyn.get("template") or dyn.get("user"):
            dyn["enabled"] = True


class RotatingSession:
    """每 N 个账号换一次出口 IP 的会话管理器(survival/refresh 复用)。

    build_dynamic_proxy(cfg) 本身每次产新随机 sid → 触发轮换时重建即换出口。
    """

    def __init__(self, cfg: dict[str, Any], rotate: int = 8):
        self.cfg = cfg
        self.rotate = max(1, int(rotate or 1))
        self.resolved = None
        self.sess: BrowserSession | None = None
        self.sid = ""
        self.rotated = False  # 最近一次 get() 是否重建(换出口)了会话(含首次建会话)
        self.is_first = False  # 最近一次 get() 是否首个会话(首次建, 非真正轮换)

    def get(self, index: int) -> BrowserSession:
        """index 从 1 开始。每 rotate 个重建会话(换出口 IP)。

        调用方可用 self.rotated 判断本次是否重建(首次建会话也算),
        self.is_first 区分"首个会话" vs "真正轮换"——避免首个账号误报轮换。
        """
        self.is_first = self.resolved is None
        self.rotated = self.is_first or (index - 1) % self.rotate == 0
        if self.rotated:
            self.force_rotate()
        assert self.sess is not None
        return self.sess

    def force_rotate(self) -> BrowserSession:
        """强制换出口 IP(新随机 sid)并重建会话——坏隧道快速重试用。"""
        if self.resolved is not None:
            try:
                self.resolved.close()
            except Exception:
                pass
            self.resolved = None
        new_url = build_dynamic_proxy(self.cfg)  # 新随机 sid
        self.resolved = resolve_proxy(self.cfg, override=new_url)
        self.sess = BrowserSession(self.cfg, proxy=self.resolved.session_url)
        self.sid = self.resolved.sid or "?"
        self.rotated = True
        self.is_first = False
        return self.sess

    def close(self) -> None:
        if self.resolved is not None:
            self.resolved.close()
            self.resolved = None
        self.sess = None


def age_h_float(ts: str) -> float:
    """ISO 时间戳 → 存活小时数(浮点); 失败返回 -1。"""
    try:
        t = datetime.fromisoformat(str(ts))
        return (time.time() - t.timestamp()) / 3600
    except Exception:
        return -1.0


def age_h(h: float) -> str:
    """存活小时数 → 可读(分钟/小时/天)。"""
    if h < 0:
        return "?"
    if h < 1:
        return f"{h*60:.0f}m"
    if h < 24:
        return f"{h:.1f}h"
    return f"{h/24:.0f}d"


def age_str(ts: str) -> str:
    """时间戳 → 可读年龄字符串(存活展示)。"""
    return age_h(age_h_float(ts))


def ts_str(d: dict) -> str:
    """记录时间戳: 优先 saved_at, 退化 updated_at。"""
    return str(d.get("saved_at") or d.get("updated_at") or "")


def account_cookie_header(account: dict[str, Any]) -> str:
    """构造账号 Cookie 头(session_token + 存好的 cookies),移植 register-kit cookie_header_from_account。"""
    parts: list[str] = []
    session_token = str(account.get("session_token") or "").strip()
    if session_token:
        parts.append("__Secure-next-auth.session-token=" + session_token)
    cookies = account.get("cookies")
    if isinstance(cookies, dict):
        for k, v in cookies.items():
            if v not in (None, "") and str(k) != "__Secure-next-auth.session-token":
                parts.append(str(k) + "=" + str(v))
    elif isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict):
                k, v = c.get("name"), c.get("value")
                if k and v not in (None, "") and str(k) != "__Secure-next-auth.session-token":
                    parts.append(str(k) + "=" + str(v))
    return "; ".join(parts)


def account_api_headers(sess: BrowserSession, account: dict[str, Any], token: str,
                        target_path: str, locale: str = "en-US", tz: str = "") -> dict[str, str]:
    """register-kit 对齐的账号请求头(在 BrowserSession 指纹之上补身份头)。

    关键差异(vs 直接 chatgpt_headers):
    - OAI-Device-Id / OpenAI-Sentinel-Device-Id / oai-device-id 用账号存的 device_id(非新随机)
    - 注入 session_token + cookies 的 Cookie 头
    - 补 X-OpenAI-Target-Path/Route + OAI-Language/OAI-Timezone + Origin
    """
    h = sess.chatgpt_headers(referer="https://chatgpt.com/")
    h["authorization"] = f"Bearer {token}"
    h["accept"] = "application/json, text/plain, */*"
    h["origin"] = "https://chatgpt.com"
    h["X-OpenAI-Target-Path"] = target_path
    h["X-OpenAI-Target-Route"] = target_path
    h["OAI-Language"] = locale
    h["accept-language"] = f"{locale},en;q=0.9"
    if tz:
        h["OAI-Timezone"] = tz
    device_id = str(account.get("device_id") or "").strip()
    if device_id:
        h["OAI-Device-Id"] = device_id
        h["OpenAI-Sentinel-Device-Id"] = device_id
        h["oai-device-id"] = device_id
    ck = account_cookie_header(account)
    if ck:
        h["Cookie"] = ck
    return h

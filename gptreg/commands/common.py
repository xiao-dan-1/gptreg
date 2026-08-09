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
        self.rotated = False  # 最近一次 get() 是否重建(换出口)了会话

    def get(self, index: int) -> BrowserSession:
        """index 从 1 开始。每 rotate 个重建会话(换出口 IP)。

        调用方可用 self.rotated 判断本次是否轮换(用于打印"新出口")。
        """
        self.rotated = self.resolved is None or (index - 1) % self.rotate == 0
        if self.rotated:
            if self.resolved is not None:
                self.resolved.close()
            new_url = build_dynamic_proxy(self.cfg)  # 新随机 sid
            self.resolved = resolve_proxy(self.cfg, override=new_url)
            self.sess = BrowserSession(self.cfg, proxy=self.resolved.session_url)
            self.sid = self.resolved.sid or "?"
        assert self.sess is not None
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

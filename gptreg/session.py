"""curl_cffi 会话封装：统一 cookie / 头 / TLS 指纹 / 代理。"""
from __future__ import annotations

import hashlib
import random
import uuid
from typing import Any

from curl_cffi.requests import Session


def _datadog_rum_headers() -> dict[str, str]:
    """对齐 starmiaoa/k12：auth JSON API 带 Datadog RUM 追踪头。

    参考：chatgpt-register-k12 `register/headers.py` `_make_trace_headers`、
    本地 k12-register-dist `lib/utils.generate_datadog_trace`。
    注释称 OpenAI backend 期望这些头；我们此前 auth 请求完全缺失。
    """
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    trace_hex = format(int(trace_id), "016x")
    parent_hex = format(int(parent_id), "016x")
    return {
        "traceparent": f"00-0000000000000000{trace_hex}-{parent_hex}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


_CHROME_VERSIONS = [104, 110, 116, 119, 120, 123, 124, 131, 136, 142, 145]


def _pick_impersonate_profile(device_id: str) -> dict[str, str]:
    """按 device_id 确定性派生 chrome 版本(impersonate + UA + sec-ch-ua 主版本对齐)。

    目的(2026-08-13): 打破所有账号 TLS 指纹(JA3)雷同。此前所有账号固定同一个
    impersonate → accounts/check 的 pthdnu 字段完全相同 → 被 OpenAI 聚类为批量注册。
    不同 device_id → 不同 chrome 版本 → 不同 TLS 指纹。
    """
    h = int(hashlib.md5(str(device_id).encode()).hexdigest()[:8], 16)
    v = _CHROME_VERSIONS[h % len(_CHROME_VERSIONS)]
    return {
        "impersonate": f"chrome{v}",
        "user_agent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      f"(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36",
        "sec_ch_ua": f'"Chromium";v="{v}", "Google Chrome";v="{v}", "Not_A Brand";v="99"',
    }


class BrowserSession:
    """模拟 Chrome 的 HTTP 会话，device_id 贯穿整条注册链。"""

    def __init__(self, cfg: dict[str, Any], proxy: str = ""):
        browser = cfg.get("browser", {})
        protocol = cfg.get("protocol", {})
        self.cfg = cfg
        self.proxy = proxy or ""
        self.device_id = str(uuid.uuid4())
        self.auth_session_logging_id = str(uuid.uuid4())
        self.sec_ch_ua_platform = browser.get("sec_ch_ua_platform", '"macOS"')
        self.sec_ch_ua_mobile = browser.get("sec_ch_ua_mobile", "?0")
        self.accept_language = browser.get(
            "accept_language", "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"
        )
        # TLS 指纹差异化(2026-08-13): impersonate_rotate=true 时按 device_id 派生
        # chrome 版本, UA/sec-ch-ua 与 impersonate 主版本对齐(避免 UA 与 TLS 指纹矛盾)。
        impersonate = browser.get("impersonate", "chrome131")
        user_agent = browser.get("user_agent", "")
        sec_ch_ua = browser.get("sec_ch_ua", "")
        if browser.get("impersonate_rotate", False):
            prof = _pick_impersonate_profile(self.device_id)
            impersonate = prof["impersonate"]
            user_agent = prof["user_agent"]
            sec_ch_ua = prof["sec_ch_ua"]
        self.user_agent = user_agent
        self.sec_ch_ua = sec_ch_ua
        self.impersonate = impersonate
        self.timeout = int(browser.get("request_timeout", 60))
        self.sentinel_sv = protocol.get("sentinel_sv", "20260219f9f6")

        self.session = Session(impersonate=self.impersonate, verify=False)
        self.session.timeout = self.timeout
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        # 贯穿全程的设备 cookie（oai-did）
        try:
            for domain in (
                ".chatgpt.com",
                "chatgpt.com",
                ".openai.com",
                "auth.openai.com",
                "sentinel.openai.com",
            ):
                self.session.cookies.set("oai-did", self.device_id, domain=domain)
        except Exception:
            pass

    def _common(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
            "sec-ch-ua-mobile": self.sec_ch_ua_mobile,
            "accept-language": self.accept_language,
        }

    def set_oai_sc(self, challenge_token: str) -> None:
        """可选：写入 oai-sc cookie（starmiaoa 生成 ``0``+c；registrar 未必用）。"""
        c = (challenge_token or "").strip()
        if not c:
            return
        val = c if c.startswith("0") else f"0{c}"
        try:
            for domain in ("auth.openai.com", ".openai.com", "sentinel.openai.com"):
                self.session.cookies.set("oai-sc", val, domain=domain)
        except Exception:
            pass

    def chatgpt_headers(self, referer: str = "https://chatgpt.com/login") -> dict[str, str]:
        h = self._common()
        h.update(
            {
                "accept": "*/*",
                "content-type": "application/json",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": referer,
                "priority": "u=1, i",
            }
        )
        return h

    def auth_api_headers(self, referer: str, flow_invocation: bool = False) -> dict[str, str]:
        h = self._common()
        h.update(
            {
                "accept": "application/json",
                "content-type": "application/json",
                "cache-control": "no-cache",
                "pragma": "no-cache",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": referer,
                "origin": "https://auth.openai.com",
                "oai-device-id": self.device_id,
                "priority": "u=1, i",
            }
        )
        # 每次请求新 trace（对齐 starmiaoa json_headers）
        h.update(_datadog_rum_headers())
        # 状态推进类端点带每请求全新 invocation-id(register-kit _common_headers 对齐)
        if flow_invocation:
            h["x-access-flow-invocation-id"] = str(uuid.uuid4())
        return h

    def auth_navigate_headers(self, referer: str = "https://chatgpt.com/") -> dict[str, str]:
        h = self._common()
        h.update(
            {
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
                "sec-fetch-site": "cross-site",
                "sec-fetch-mode": "navigate",
                "sec-fetch-dest": "document",
                "referer": referer,
                "priority": "u=0, i",
                "upgrade-insecure-requests": "1",
            }
        )
        return h

    def sentinel_headers(self) -> dict[str, str]:
        h = self._common()
        h.update(
            {
                "accept": "*/*",
                "content-type": "text/plain;charset=UTF-8",
                "origin": "https://sentinel.openai.com",
                "referer": (
                    f"https://sentinel.openai.com/backend-api/sentinel/frame.html"
                    f"?sv={self.sentinel_sv}"
                ),
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "priority": "u=1, i",
            }
        )
        return h

    def get(self, url: str, headers: dict | None = None, **kwargs):
        return self.session.get(url, headers=headers, **kwargs)

    def post(self, url: str, headers: dict | None = None, **kwargs):
        return self.session.post(url, headers=headers, **kwargs)

    def proxy_label(self) -> str:
        if not self.proxy:
            return "直连"
        # 若调用方挂了脱敏标签，优先用
        label = getattr(self, "_proxy_label", "") or ""
        if label:
            return label
        try:
            from gptreg.proxyutil import proxy_label as _pl

            return _pl(self.proxy)
        except Exception:
            try:
                scheme = self.proxy.split("://", 1)[0]
                host = self.proxy.split("@")[-1]
                return f"{scheme}://***@{host}"
            except Exception:
                return "已配置"

    def close(self) -> None:
        """显式关闭底层 curl_cffi Session(连接池), 避免批量长跑累积连接/句柄。"""
        try:
            self.session.close()
        except Exception:
            pass


def set_cookies(session: BrowserSession, cookies: list[dict]) -> None:
    """把 cookies 列表注入会话(续期/登录复用: 有 cookies 就能重抓 /api/auth/session)。

    refresh/backfill 复用(替代各自 _cookie_jar)。
    """
    for c in cookies or []:
        try:
            session.session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain"))
        except Exception:
            pass


def jar_to_list(session: BrowserSession) -> list[dict]:
    """导出会话 cookie jar 为可落盘列表(与 set_cookies 对称)。"""
    return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
             "secure": bool(getattr(c, "secure", False))}
            for c in session.session.cookies.jar]

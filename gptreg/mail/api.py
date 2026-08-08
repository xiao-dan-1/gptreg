"""通用第三方 API 接码插件（配置端点/请求体/OTP 路径即可接入, 无需写代码）。

统一框架的一部分:
  - MailSource 插件: 号池行 `email----api_key` → mail_type="api"
  - MailClient 插件: 按 config mail.api_client 配置轮询第三方 API 拉验证码

config.yaml 配置示例:
  mail:
    api_client:
      endpoint: "https://api.xxx.com/fetch"          # 必填: 收码 API URL
      method: "POST"                                   # POST / GET
      request_body: '{"api_key":"{api_key}","email":"{email}","mailbox":"INBOX"}'
      otp_path: "data.code"                            # 响应里 OTP 的 JSON 路径(空=通用扫描)
      interval: 3                                      # 轮询间隔
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from curl_cffi import requests as cr

from gptreg.mail.base import MailClient, MailSource
from gptreg.mail.otp_cache import MailClientError
from gptreg.otp import extract_code_from_any

logger = logging.getLogger(__name__)


def _lookup_path(data: Any, path: str) -> Any:
    """按点分路径取嵌套值, 如 data.code → data["code"], data.list.0.code。"""
    val = data
    for part in path.split("."):
        if isinstance(val, dict):
            val = val.get(part)
        elif isinstance(val, list) and part.isdigit():
            idx = int(part)
            val = val[idx] if idx < len(val) else None
        else:
            return None
    return val


class ApiMailClient(MailClient):
    """通用第三方 API 接码: 配置端点 + 请求/响应解析规则即可, 无需写代码。"""

    def __init__(
        self,
        account: dict[str, Any],
        cfg: dict[str, Any] | None = None,
        proxy: str | None = None,
        impersonate: str = "chrome142",
        timeout: int = 30,
    ):
        self.email = account.get("email") or ""
        self.api_key = account.get("api_key") or ""
        self.proxy = proxy
        self.impersonate = impersonate
        self.timeout = timeout
        ac = ((cfg or {}).get("mail") or {}).get("api_client") or {}
        self.endpoint = str(ac.get("endpoint") or "")
        self.method = str(ac.get("method") or "POST").upper()
        self.request_body = str(ac.get("request_body") or "")
        self.otp_path = str(ac.get("otp_path") or "")
        self.interval = max(1, int(ac.get("interval") or 3))

    def _request_code(self) -> str | None:
        body = (
            self.request_body
            .replace("{email}", self.email)
            .replace("{api_key}", self.api_key)
        )
        kwargs: dict[str, Any] = {
            "timeout": self.timeout,
            "impersonate": self.impersonate,
        }
        if self.proxy:
            kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        try:
            if self.method == "GET":
                params = json.loads(body) if body else None
                r = cr.get(self.endpoint, params=params, **kwargs)
            else:
                r = cr.post(
                    self.endpoint, data=body,
                    headers={"Content-Type": "application/json"}, **kwargs,
                )
        except Exception as exc:
            logger.warning("[ApiMail] 请求失败: %s", exc)
            return None
        if r.status_code != 200:
            logger.warning("[ApiMail] HTTP %s: %s", r.status_code, (r.text or "")[:120])
            return None
        try:
            data = r.json()
        except Exception:
            data = r.text or ""
        if self.otp_path:
            val = _lookup_path(data, self.otp_path)
            s = str(val or "")
            if s and s.strip().isdigit():
                return s.strip()
        return extract_code_from_any(data)

    def wait_for_otp(
        self,
        after_ts: float | None = None,
        timeout: int = 90,
        interval: int = 3,
        settle_seconds: int = 5,
        exclude_codes: set[str] | None = None,
        on_poll: Callable[[dict], None] | None = None,
    ) -> str:
        del settle_seconds  # API 收码通常无 settle
        if not self.endpoint:
            raise MailClientError("ApiMail 未配置端点 (config mail.api_client.endpoint)")
        exclude = set(str(c) for c in (exclude_codes or set()))
        deadline = time.time() + timeout
        interval = max(self.interval, interval)
        t_start = time.time()
        while time.time() < deadline:
            code = self._request_code()
            if code and str(code) not in exclude:
                if on_poll:
                    try:
                        on_poll({"code": str(code), "excluded": False, "source": "api",
                                 "elapsed_s": round(time.time() - t_start, 1)})
                    except Exception:
                        pass
                logger.info("[ApiMail] 到件 OTP=%s 延迟 %.1fs（email=%s）", code,
                            time.time() - t_start, self.email)
                return str(code)
            time.sleep(interval)
        raise MailClientError(f"等待 {self.email} OTP 超时（>{timeout}s）")


class ApiMailSource(MailSource):
    """通用 API 号源: 号池行 `email----api_key`(第二段非 URL, 区别于 icloud 接码 URL)。"""

    name = "api"

    def parse_line(self, raw: str) -> dict[str, Any] | None:
        parts = raw.split("----")
        if len(parts) != 2:
            return None
        email, api_key = parts[0].strip(), parts[1].strip()
        if not email or "@" not in email or not api_key:
            return None
        if api_key.startswith(("http://", "https://")):
            return None  # 那是 icloud(URL code_url)
        return {
            "email": email,
            "api_key": api_key,
            "mail_type": "api",
            "raw_line": raw,
        }

    def build_client(
        self, account: dict, *, proxy: str | None = None,
        impersonate: str = "chrome142", cfg: dict | None = None,
    ) -> MailClient:
        return ApiMailClient(account, cfg or {}, proxy=proxy, impersonate=impersonate)

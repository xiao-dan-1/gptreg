"""外部收码服务: Gmail get-code / XDAuv。"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

from curl_cffi import requests as cr

from gptreg.mail.otp_cache import MailClientError, _parse_ts
from gptreg.otp import extract_code_from_any

logger = logging.getLogger(__name__)

class GmailApiClient:
    """mailsapi 等 get-code 接口。"""

    def __init__(
        self,
        account: dict[str, Any],
        proxy: str | None = None,
        impersonate: str = "chrome142",
        timeout: int = 25,
    ):
        self.email = account["email"]
        self.code_url = account["code_url"]
        self.password = account.get("password", "")
        self.client_id = account.get("client_id", "")
        self.refresh_token = account.get("refresh_token", "")
        self.proxy = proxy or None
        self.impersonate = impersonate
        self.timeout = timeout

    def _fetch_code(self) -> str | None:
        try:
            kwargs: dict[str, Any] = {
                "timeout": self.timeout,
                "impersonate": self.impersonate,
                "headers": {"Accept": "application/json,text/plain,*/*"},
            }
            if self.proxy:
                kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
            r = cr.get(self.code_url, **kwargs)
            if r.status_code != 200:
                return None
            try:
                return extract_code_from_any(r.json())
            except Exception:
                return extract_code_from_any(r.text)
        except Exception:
            return None

    def wait_for_otp(
        self,
        after_ts: float | None = None,
        timeout: int = 90,
        interval: int = 3,
        settle_seconds: int = 0,
        exclude_codes: set[str] | None = None,
        on_poll: Callable[[dict], None] | None = None,
    ) -> str:
        del after_ts, settle_seconds  # get-code 通常无时间戳
        exclude = set(str(c) for c in (exclude_codes or set()))
        deadline = time.time() + timeout
        reported: set[tuple[str, bool]] = set()
        while time.time() < deadline:
            code = self._fetch_code()
            if code:
                excluded = str(code) in exclude
                marker = (str(code), excluded)
                if on_poll and marker not in reported:
                    reported.add(marker)
                    try:
                        on_poll({"code": str(code), "excluded": excluded, "source": "gmail_api"})
                    except Exception:
                        pass
                if not excluded:
                    return str(code)
            time.sleep(interval)
        raise MailClientError(f"等待 {self.email} OTP 超时（>{timeout}s，可能只有旧码）")

class XDAuvMailClient:
    """outlook.xdauv.xyz 服务收码（海外干净 IP，解决本地 IMAP 部分账号被 MS 拒）。

    服务端海外部署对全部号池账号收码成功(~8s)，包括本地 IMAP 连不上的账号
    (authenticated but not connected = MS 对部分账号要求干净可信 IP)。
    端点可在 config mail.xdauv_endpoint 配置，方便后续替换专用接码 API。
    """

    def __init__(
        self,
        account: dict[str, Any],
        proxy: str | None = None,
        impersonate: str = "chrome142",
        timeout: int = 30,
        cfg: dict[str, Any] | None = None,
    ):
        self.email = account.get("email") or ""
        self.account = account
        self.proxy = proxy or None
        self.impersonate = impersonate
        self.timeout = timeout
        self._cfg = cfg or {}
        self.endpoint = self._endpoint()

    def _endpoint(self) -> str:
        ep = (self._cfg.get("mail") or {}).get("xdauv_endpoint") or ""
        if ep:
            return ep
        try:
            from gptreg.config import load_config
            return (load_config().get("mail") or {}).get("xdauv_endpoint") \
                or "https://outlook.xdauv.xyz/api/fetch"
        except Exception:
            return "https://outlook.xdauv.xyz/api/fetch"

    def _proxies(self) -> dict | None:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def _account_text(self) -> str:
        raw = self.account.get("raw_line") or ""
        if raw:
            return raw
        return "----".join([
            str(self.account.get("email", "")),
            str(self.account.get("password", "")),
            str(self.account.get("client_id", "")),
            str(self.account.get("refresh_token", "")),
        ])

    def _fetch(self) -> list[dict]:
        payload = {
            "account_text": self._account_text(),
            "mailbox": "INBOX",
            "limit": 20,
            "filter_recipient": True,
        }
        r = cr.post(self.endpoint, json=payload, timeout=self.timeout,
                    impersonate=self.impersonate, proxies=self._proxies())
        if r.status_code != 200:
            raise MailClientError(f"XDAuv /api/fetch HTTP {r.status_code}: {(r.text or '')[:150]}")
        d = r.json()
        for row in d.get("rows", []) or []:
            if not row.get("ok"):
                raise MailClientError(f"XDAuv 账号失败: {str(row.get('error'))[:150]}")
        return d.get("messages", []) or []

    def wait_for_otp(
        self,
        after_ts: float | None = None,
        timeout: int = 150,
        interval: int = 3,
        settle_seconds: int = 5,
        exclude_codes: set[str] | None = None,
        on_poll: Callable[[dict], None] | None = None,
    ) -> str:
        exclude = set(str(c) for c in (exclude_codes or set()))
        deadline = time.time() + timeout
        t_start = time.time()
        reported: set[tuple[str, bool]] = set()
        mid_warned = False
        while time.time() < deadline:
            try:
                msgs = self._fetch()
            except Exception as exc:
                logger.warning("[XDAuv] fetch 异常: %s", exc)
                time.sleep(interval)
                continue
            for m in msgs:
                ts = _parse_ts(m.get("sent_at") or "")
                if after_ts and ts and ts < after_ts:
                    continue
                item = {
                    "subject": m.get("subject") or "",
                    "text": m.get("body_preview") or "",
                    "content": m.get("body_preview") or "",
                }
                otp = extract_otp(item)
                if not otp:
                    continue
                marker = (otp, otp in exclude)
                if on_poll and marker not in reported:
                    reported.add(marker)
                    try:
                        on_poll({"code": otp, "excluded": otp in exclude, "source": "xdauv",
                                 "elapsed_s": round(time.time() - t_start, 1)})
                    except Exception:
                        pass
                delay = time.time() - t_start
                logger.info("[XDAuv] 到件 OTP=%s 延迟 %.1fs (email=%s)", otp, delay, self.email)
                if settle_seconds > 0:
                    time.sleep(settle_seconds)
                return otp
            if not mid_warned and time.time() - t_start > timeout / 2:
                mid_warned = True
                logger.info("[XDAuv] 等待 %s OTP 已 %.0fs 仍无新邮件", self.email, time.time() - t_start)
            time.sleep(interval)
        raise MailClientError(f"XDAuv 等待 {self.email} OTP 超时（>{timeout}s）")

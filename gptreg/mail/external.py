"""外部收码服务: iCloud 接码 HTML 收件箱 / XDAuv。"""
from __future__ import annotations

import html
import logging
import re
import time
from typing import Any, Callable

from curl_cffi import requests as cr

from gptreg.mail.base import MailClient
from gptreg.mail.otp_cache import MailClientError, _parse_ts

logger = logging.getLogger(__name__)


def _clean_html_fragment(x: str) -> str:
    """HTML 片段 → 纯文本(去 script/style/标签, 解实体, 压空白)。"""
    if not x:
        return ""
    x = re.sub(r"<script[^>]*>.*?</script>", "", x, flags=re.S)
    x = re.sub(r"<style[^>]*>.*?</style>", "", x, flags=re.S)
    x = re.sub(r"<[^>]+>", " ", x)
    return re.sub(r"\s+", " ", html.unescape(x)).strip()


def parse_icloud_inbox(body: str) -> list[dict]:
    """解析 icloud-api.top HTML 收件箱 → 邮件列表。

    页面结构(实测):
      <div class="cnt">N 封</div>
      <div class="card"><div class="fr">发件</div><div class="su">主题</div>
        <div class="dt">时间</div><div class="bd">...嵌套邮件HTML正文...</div></div>
    .bd 内嵌整封邮件 HTML(含 <html><style>), 提取文本后验证码在正文里。
    """
    cards = re.findall(r'class="card"(.*?)(?=class="card"|</body>)', body, flags=re.S)
    mails = []
    for c in cards:
        fr = re.search(r'class="fr">(.*?)</div>', c, flags=re.S)
        su = re.search(r'class="su">(.*?)</div>', c, flags=re.S)
        dt = re.search(r'class="dt">(.*?)</div>', c, flags=re.S)
        # 正文: 从 <div class="bd"> 到卡片结束(可能内嵌完整邮件HTML, 用 class="no" 或 </body> 边界)
        bd_i = c.find('class="bd">')
        bd_raw = c[bd_i + len('class="bd">'):] if bd_i >= 0 else ""
        bd_raw = bd_raw.split('class="no"')[0]
        mails.append({
            "sender": _clean_html_fragment(fr.group(1)) if fr else "",
            "subject": _clean_html_fragment(su.group(1)) if su else "",
            "date": _clean_html_fragment(dt.group(1)) if dt else "",
            "body_text": _clean_html_fragment(bd_raw),
        })
    return mails


def _extract_otp_from_text(text: str) -> str | None:
    """从正文纯文本提取 6 位验证码(注册 OTP)。"""
    if not text:
        return None
    m = re.search(r"\b(\d{6})\b", text)
    return m.group(1) if m else None


class ICloudApiClient(MailClient):
    """iCloud 接码 HTML 收件箱: 号池行 `email----https://code-url`。

    实测该服务返回 HTML 收件箱页(非 JSON):
      <div class="cnt">0 封</div> 邮件数, .card 每封邮件, .bd 内嵌邮件HTML正文。
    GET code_url 每次返回最新状态, 邮件到达后 .cnt 增加且 .card 出现新邮件。
    """

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

    def _get_page(self) -> str:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout,
            "impersonate": self.impersonate,
            "headers": {"Accept": "application/json,text/plain,*/*"},
        }
        if self.proxy:
            kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        r = cr.get(self.code_url, **kwargs)
        if r.status_code != 200:
            raise MailClientError(f"icloud 接码 HTTP {r.status_code}: {(r.text or '')[:120]}")
        return r.text

    def _find_new_otp(self, after_ts: float | None = None) -> str | None:
        """拉取收件箱页, 找 after_ts 之后邮件的验证码。"""
        body = self._get_page()
        for ml in parse_icloud_inbox(body):
            # 时间过滤: 只取新邮件(防取旧码)
            if after_ts is not None:
                ts = _parse_ts(ml.get("date") or "")
                if ts and ts < after_ts:
                    continue
            otp = _extract_otp_from_text(ml.get("body_text") or "")
            if otp:
                return otp
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
        exclude = set(str(c) for c in (exclude_codes or set()))
        deadline = time.time() + timeout
        t_start = time.time()
        reported: set[tuple[str, bool]] = set()
        mid_warned = False
        while time.time() < deadline:
            try:
                code = self._find_new_otp(after_ts)
            except Exception as exc:
                logger.warning("[iCloud] 拉码异常 email=%s: %s", self.email, exc)
                time.sleep(interval)
                continue
            if code:
                excluded = str(code) in exclude
                marker = (str(code), excluded)
                if on_poll and marker not in reported:
                    reported.add(marker)
                    try:
                        on_poll({"code": str(code), "excluded": excluded, "source": "icloud",
                                 "elapsed_s": round(time.time() - t_start, 1)})
                    except Exception:
                        pass
                if not excluded:
                    logger.info("[iCloud] 到件 OTP=%s 延迟 %.1fs (email=%s)", code,
                                time.time() - t_start, self.email)
                    if settle_seconds > 0:
                        time.sleep(settle_seconds)
                    return str(code)
            if not mid_warned and time.time() - t_start > timeout / 2:
                mid_warned = True
                logger.info("[iCloud] 等待 %s OTP 已 %.0fs 仍无新邮件", self.email, time.time() - t_start)
            time.sleep(interval)
        raise MailClientError(f"icloud 等待 {self.email} OTP 超时（>{timeout}s，可能只有旧码）")

class XDAuvMailClient(MailClient):
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

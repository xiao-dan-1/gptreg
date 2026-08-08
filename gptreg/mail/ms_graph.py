"""Graph API 收码（IMAP 不可用时降级兜底）。"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from curl_cffi import requests as cr

from gptreg.mail.base import MailClient
from gptreg.mail.otp_cache import MAIL_ENDPOINT, TOKEN_ENDPOINT, MailClientError, _parse_ts
from gptreg.otp import extract_otp, looks_like_openai_email

logger = logging.getLogger(__name__)

class MSMailClient(MailClient):
    """Outlook REST v2 + login.live.com refresh_token。"""

    def __init__(
        self,
        account: dict[str, Any],
        proxy: str | None = None,
        impersonate: str = "chrome142",
        timeout: int = 25,
    ):
        self.email = account["email"]
        self.password = account.get("password", "")
        self.client_id = account["client_id"]
        self.refresh_token = account["refresh_token"]
        self.proxy = proxy or None
        self.impersonate = impersonate
        self.timeout = timeout
        self._access_token: str | None = None
        self._access_token_exp = 0.0

    def _proxies(self) -> dict | None:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def get_access_token(self, force: bool = False) -> str | None:
        now = time.time()
        if not force and self._access_token and now < self._access_token_exp - 60:
            return self._access_token
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        try:
            r = cr.post(
                TOKEN_ENDPOINT,
                data=data,
                timeout=self.timeout,
                impersonate=self.impersonate,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                proxies=self._proxies(),
            )
            j = r.json() if r.status_code == 200 else {}
        except Exception as exc:
            logger.warning("[MSMail] token 请求失败: %s", exc)
            return None
        at = j.get("access_token")
        if not at:
            logger.warning("[MSMail] 未拿到 access_token: %s", str(j)[:200])
            return None
        new_rt = j.get("refresh_token")
        if new_rt:
            self.refresh_token = new_rt
        self._access_token = at
        self._access_token_exp = now + int(j.get("expires_in") or 3600)
        return at

    def _fetch_messages(self, top: int = 2, after_ts: float | None = None) -> list[dict]:
        at = self.get_access_token()
        if not at:
            return []
        params = {
            # $select 只取需要的字段(微软官方最推荐优化项; IsRead 未用已剔除)
            "$select": "Id,Subject,From,BodyPreview,Body,ReceivedDateTime",
            "$top": str(top),
            "$orderby": "ReceivedDateTime desc",
        }
        if after_ts:
            # 只拉发码窗口内的邮件, 减小每轮响应/解析;
            # 留 60s 容差防本地时钟领先 MS 导致新邮件被判旧。
            # $filter/$orderby 组合规则: orderby 属性必须在 filter 中且顺序一致(均 receivedDateTime), 否则 InefficientFilter
            from datetime import datetime, timezone

            iso = datetime.fromtimestamp(after_ts - 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            params["$filter"] = f"ReceivedDateTime ge {iso}"
        # Prefer: body 以纯文本返回(非 HTML), 显著减小响应 payload——我们只需文本抽 OTP
        headers = {
            "Authorization": f"Bearer {at}",
            "Accept": "application/json",
            "Prefer": 'outlook.body-content-type="text"',
        }
        try:
            r = cr.get(
                MAIL_ENDPOINT,
                params=params,
                timeout=self.timeout,
                impersonate=self.impersonate,
                headers=headers,
                proxies=self._proxies(),
            )
            if r.status_code == 401:
                at = self.get_access_token(force=True)
                if not at:
                    return []
                headers["Authorization"] = f"Bearer {at}"
                r = cr.get(
                    MAIL_ENDPOINT,
                    params=params,
                    timeout=self.timeout,
                    impersonate=self.impersonate,
                    headers=headers,
                    proxies=self._proxies(),
                )
            j = r.json() if r.status_code == 200 else {}
        except Exception as exc:
            logger.warning("[MSMail] 拉邮件失败: %s", exc)
            return []
        return j.get("value") or []

    @staticmethod
    def _normalize_msg(msg: dict) -> dict:
        # 统一成 extract_otp / looks_like_openai_email 可识别的字段
        from_addr = ((msg.get("From") or {}).get("EmailAddress") or {}).get("Address") or ""
        body = msg.get("Body") if isinstance(msg.get("Body"), dict) else {}
        return {
            "subject": msg.get("Subject") or "",
            "from": from_addr,
            "text": msg.get("BodyPreview") or "",
            "content": body.get("Content") or "",
            "date": msg.get("ReceivedDateTime") or "",
            "id": msg.get("Id"),
            "_raw": msg,
        }

    def wait_for_otp(
        self,
        after_ts: float | None = None,
        timeout: int = 90,
        interval: int = 3,
        settle_seconds: int = 5,
        exclude_codes: set[str] | None = None,
        on_poll: Callable[[dict], None] | None = None,
    ) -> str:
        exclude = set(str(c) for c in (exclude_codes or set()))
        deadline = time.time() + timeout
        best_otp: str | None = None
        best_ts = 0.0
        settle_until: float | None = None
        reported: set[tuple[str, bool]] = set()

        _last_progress_log = 0.0
        while time.time() < deadline:
            _now = time.time()
            # Graph 新邮件有 ~150s 索引延迟, 周期性报告进度, 避免用户看静默等待
            if _now - _last_progress_log >= 30:
                _last_progress_log = _now
                logger.info(
                    "[MSMail/Graph] 等待中 t+%.0fs (Graph 新邮件索引延迟可达 150s), after=%s",
                    _now - (after_ts or _now),
                    time.strftime("%H:%M:%S", time.localtime(after_ts or _now)),
                )
            for msg in self._fetch_messages(after_ts=after_ts):
                item = self._normalize_msg(msg)
                if not looks_like_openai_email(item):
                    continue
                ts = _parse_ts(item.get("date") or "")
                if after_ts is not None and ts and ts < after_ts - 30:
                    logger.debug("[OTP/diag] 跳过旧邮件 date=%s from=%s", item.get("date"), item.get("from"))
                    continue
                otp = extract_otp(item)
                if not otp:
                    continue
                logger.debug(
                    "[OTP/diag] 发现 OTP 候选 t+%.1fs date=%s from=%s subj=%s",
                    _now - (after_ts or 0),
                    item.get("date"),
                    item.get("from"),
                    item.get("subject")[:40],
                )
                excluded = otp in exclude
                marker = (otp, excluded)
                if on_poll and marker not in reported:
                    reported.add(marker)
                    try:
                        on_poll({"code": otp, "excluded": excluded, "source": "ms_oauth"})
                    except Exception:
                        pass
                # 关键修复：同主号的新 alias 可能收到相同验证码(OpenAI 复用码)，
                # 若仅因码值在 exclude 就跳过，会死等一封不存在的"新码"。
                # 只有"旧邮件"(ts 在 after_ts 附近但早于发码)才应排除；
                # 本次新发的邮件(ts >= after_ts)即使码重复也直接采用。
                is_new = after_ts is None or ts >= after_ts
                if excluded and not is_new:
                    continue
                if ts >= best_ts:
                    if best_otp and otp != best_otp:
                        logger.info("[MSMail] 发现更新 OTP=%s，替换 %s", otp, best_otp)
                    best_otp = otp
                    best_ts = ts or best_ts
                    settle_until = time.time() + settle_seconds

            now = time.time()
            if best_otp and settle_until is not None and now >= settle_until:
                logger.info(
                    "[MSMail/Graph] 到件 OTP=%s 延迟 %.1fs（email=%s）",
                    best_otp, now - (after_ts or now), self.email,
                )
                return best_otp
            time.sleep(interval)

        if best_otp:
            return best_otp
        raise MailClientError(f"等待 {self.email} OTP 超时（>{timeout}s）")

"""邮箱 OTP 客户端：Outlook REST / Gmail get-code。"""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from curl_cffi import requests as cr

from gptreg.otp import extract_code_from_any, extract_otp, looks_like_openai_email

logger = logging.getLogger(__name__)

TOKEN_ENDPOINT = "https://login.live.com/oauth20_token.srf"
MAIL_ENDPOINT = "https://outlook.office.com/api/v2.0/me/messages"


class MailClientError(RuntimeError):
    pass


class UsedCodeCache:
    """共享 get-code 场景下，按 code_url / email 排除已消费 OTP。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _key_id(self, key: str) -> str:
        return hashlib.sha1(str(key or "").encode("utf-8")).hexdigest()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) or {}
            items = data.get("items") or {}
            if isinstance(items, dict):
                self._items = items
        except Exception:
            self._items = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "items": self._items}
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

    def seen_codes(self, key: str) -> set[str]:
        with self._lock:
            self._load()
            item = self._items.get(self._key_id(key)) or {}
            codes = set(str(c) for c in (item.get("codes") or []))
            latest = item.get("latest")
            if latest:
                codes.add(str(latest))
            return codes

    def remember(self, key: str, code: str, email: str = "", status: str = "seen") -> None:
        if not code:
            return
        with self._lock:
            self._load()
            kid = self._key_id(key)
            item = self._items.get(kid) or {"codes": [], "latest": "", "email": email}
            codes = [str(c) for c in (item.get("codes") or [])]
            if str(code) not in codes:
                codes.append(str(code))
            # 只保留最近 20 个，避免无限增长
            item["codes"] = codes[-20:]
            item["latest"] = str(code)
            item["email"] = email or item.get("email") or ""
            item["status"] = status
            item["updated_at"] = int(time.time())
            self._items[kid] = item
            self._save()


def mail_identity_key(account: dict[str, Any]) -> str:
    """共享收件箱应按 code_url 排除旧码，而不是按别名邮箱。"""
    return account.get("code_url") or account.get("email") or ""


class MSMailClient:
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

    def _fetch_messages(self, top: int = 25) -> list[dict]:
        at = self.get_access_token()
        if not at:
            return []
        params = {
            "$select": "Id,Subject,From,BodyPreview,Body,ReceivedDateTime,IsRead",
            "$top": str(top),
            "$orderby": "ReceivedDateTime desc",
        }
        headers = {"Authorization": f"Bearer {at}", "Accept": "application/json"}
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

        while time.time() < deadline:
            for msg in self._fetch_messages():
                item = self._normalize_msg(msg)
                if not looks_like_openai_email(item):
                    continue
                ts = _parse_ts(item.get("date") or "")
                if after_ts is not None and ts and ts < after_ts - 30:
                    continue
                otp = extract_otp(item)
                if not otp:
                    continue
                excluded = otp in exclude
                marker = (otp, excluded)
                if on_poll and marker not in reported:
                    reported.add(marker)
                    try:
                        on_poll({"code": otp, "excluded": excluded, "source": "ms_oauth"})
                    except Exception:
                        pass
                if excluded:
                    continue
                if ts >= best_ts:
                    if best_otp and otp != best_otp:
                        logger.info("[MSMail] 发现更新 OTP=%s，替换 %s", otp, best_otp)
                    best_otp = otp
                    best_ts = ts or best_ts
                    settle_until = time.time() + settle_seconds

            now = time.time()
            if best_otp and settle_until is not None and now >= settle_until:
                return best_otp
            time.sleep(interval)

        if best_otp:
            return best_otp
        raise MailClientError(f"等待 {self.email} OTP 超时（>{timeout}s）")


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


def build_mail_client(
    account: dict[str, Any],
    proxy: str | None = None,
    impersonate: str = "chrome142",
) -> MSMailClient | GmailApiClient:
    mail_type = account.get("mail_type") or "ms_oauth"
    if mail_type == "gmail_api":
        return GmailApiClient(account, proxy=proxy, impersonate=impersonate)
    return MSMailClient(account, proxy=proxy, impersonate=impersonate)


def _parse_ts(raw: str) -> float:
    if not raw:
        return 0.0
    text = str(raw).strip()
    try:
        if text.endswith("Z") or "T" in text:
            base = text.replace("Z", "")[:19]
            return float(calendar.timegm(time.strptime(base, "%Y-%m-%dT%H:%M:%S")))
        if " " in text:
            base = text[:19]
            return float(calendar.timegm(time.strptime(base, "%Y-%m-%d %H:%M:%S")))
    except Exception:
        pass
    try:
        from datetime import datetime

        return datetime.strptime(text, "%a, %d %b %Y %H:%M:%S %z").timestamp()
    except Exception:
        return 0.0

"""邮箱 OTP 客户端：Outlook REST / IMAP / Gmail get-code。"""
from __future__ import annotations

import calendar
import email
import hashlib
import imaplib
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

# IMAP 搜索 OpenAI 发件人（noreply@tm.openai.com 含 "openai"）
IMAP_OPENAI_SENDER = "openai"


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
            _now = time.time()
            for msg in self._fetch_messages():
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
                return best_otp
            time.sleep(interval)

        if best_otp:
            return best_otp
        raise MailClientError(f"等待 {self.email} OTP 超时（>{timeout}s）")


class IMAPOAuthClient:
    """Outlook IMAP + XOAUTH2（OAuth 凭据连 IMAP，绕开 Graph 索引延迟）。

    Graph API 对新邮件有 ~150s 间歇性索引延迟（实测 0.6s~152s 波动），
    IMAP 走即时搜索/UID 递增，实测稳定 0.6s 到件。号池 ms_oauth 凭据
    (refresh_token) 可直接换 access_token 做 XOAUTH2，无需额外 IMAP 密码。
    """

    IMAP_HOST = "outlook.office365.com"
    IMAP_PORT = 993

    def __init__(
        self,
        account: dict[str, Any],
        proxy: str | None = None,
        impersonate: str = "chrome142",
        timeout: int = 25,
    ):
        self.email = account["email"]
        self.client_id = account["client_id"]
        self.refresh_token = account["refresh_token"]
        self.proxy = proxy or None
        self.impersonate = impersonate
        self.timeout = timeout
        self._conn: imaplib.IMAP4_SSL | None = None
        self._access_token: str | None = None
        # Graph 兜底：部分邮箱 OAuth token 缺 IMAP scope（authenticated but not connected），
        # 这类邮箱 IMAP 连不上但 Graph 正常，需自动降级。
        self._fallback: MSMailClient | None = None

    def _graph_fallback(self) -> MSMailClient:
        if self._fallback is None:
            self._fallback = MSMailClient(
                {"email": self.email, "client_id": self.client_id,
                 "refresh_token": self.refresh_token, "password": ""},
                proxy=self.proxy, impersonate=self.impersonate, timeout=self.timeout,
            )
        return self._fallback

    def _get_access_token(self, force: bool = False) -> str | None:
        if not force and self._access_token:
            return self._access_token
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        # IMAP 直连：token 交换直连 login.live.com（不走 OpenAI 注册代理），
        # 避免链式隧道/动态代理对 curl 的影响；IMAP 993 本身也是直连。
        for proxies in (None, self._proxies() if self.proxy else None):
            try:
                r = cr.post(
                    TOKEN_ENDPOINT,
                    data=data,
                    timeout=self.timeout,
                    impersonate=self.impersonate,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    proxies=proxies,
                )
                j = r.json() if r.status_code == 200 else {}
                at = j.get("access_token")
                if at:
                    self._access_token = at
                    return at
                logger.warning("[IMAP] token 交换 status=%s: %s", r.status_code, str(j)[:150])
            except Exception as exc:
                logger.warning("[IMAP] token 请求失败(proxies=%s): %s", proxies, exc)
        return None

    def _proxies(self) -> dict | None:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def connect(self) -> imaplib.IMAP4_SSL:
        at = self._get_access_token()
        if not at:
            raise MailClientError(f"IMAP 换 access_token 失败: {self.email}")
        conn = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
        auth_str = f"user={self.email}\x01auth=Bearer {at}\x01\x01"
        conn.authenticate("XOAUTH2", lambda x: auth_str.encode())
        self._conn = conn
        return conn

    def _ensure_conn(self) -> imaplib.IMAP4_SSL:
        if self._conn is None:
            return self.connect()
        try:
            # 轻探活：若断连则重连
            typ, _ = self._conn.select("INBOX", readonly=True)
            if typ != "OK":
                self.close()
                return self.connect()
        except Exception:
            self.close()
            return self.connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def _latest_uid(self, conn: imaplib.IMAP4_SSL) -> int:
        conn.select("INBOX", readonly=True)
        typ, data = conn.search(None, "FROM", IMAP_OPENAI_SENDER)
        if typ != "OK" or not data or not data[0]:
            return 0
        ids = [int(x) for x in data[0].split()]
        return max(ids) if ids else 0

    def _latest_since(self, conn: imaplib.IMAP4_SSL, after_ts: float) -> tuple[str, int, float] | None:
        """返回最新且 Date>=after_ts 的 OpenAI 邮件 (otp, uid, date_ts)。无则 None。

        相比 UID 增量：send_otp 后邮件可能已到（初始 last_uid 已含目标邮件），
        UID 增量会漏检；用 after_ts 时间过滤能覆盖"发码前已到"的场景。
        """
        conn.select("INBOX", readonly=True)
        typ, data = conn.search(None, "FROM", IMAP_OPENAI_SENDER)
        if typ != "OK" or not data or not data[0]:
            return None
        ids = [int(x) for x in data[0].split()]
        if not ids:
            return None
        logger.debug("[IMAP/diag] _latest_since after_ts=%s ids=%s", after_ts, ids[-3:])
        # 从最新往前找，最多看最近 10 封
        for uid in reversed(ids[-10:]):
            try:
                t, md = conn.fetch(str(uid), "(BODY.PEEK[HEADER.FIELDS (DATE SUBJECT)] BODY.PEEK[TEXT])")
                if t != "OK" or not md:
                    continue
                header = b""
                body = b""
                for part in md:
                    if isinstance(part, tuple):
                        block = part[1]
                        if b"Date:" in block[:200] or b"Subject:" in block[:200]:
                            header += block
                        else:
                            body += block
                msg = email.message_from_bytes(header or b"")
                date_raw = str(msg.get("Date") or "")
                ts = _parse_ts(date_raw)
                logger.debug("[IMAP/diag] uid=%s date=%s ts=%s", uid, date_raw[:30], ts)
                if after_ts and ts and ts < after_ts:
                    # Date 早于发码 → 旧邮件，继续往前找（更旧的不可能是本次）
                    logger.debug("[IMAP/diag]   跳过旧邮件 ts=%s < after_ts=%s", ts, after_ts)
                    continue
                otp = self._extract_otp_from_raw(header, body)
                if otp:
                    return (otp, int(uid), ts)
            except Exception as exc:
                logger.warning("[IMAP] _latest_since uid=%s 异常: %s", uid, exc)
                continue
        return None

    def _extract_otp_from_raw(self, header: bytes, body: bytes) -> str | None:
        """从 IMAP header/body 提取 OTP，复用 extract_otp。"""
        try:
            msg = email.message_from_bytes(header or b"")
            body_text = ""
            try:
                m2 = email.message_from_bytes(body or b"")
                for part in m2.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True) or b""
                        body_text = payload.decode("utf-8", "replace")
                        break
                if not body_text:
                    body_text = (body or b"").decode("utf-8", "replace")
            except Exception:
                body_text = (body or b"").decode("utf-8", "replace")
            item = {
                "subject": str(msg.get("Subject") or ""),
                "from": str(msg.get("From") or ""),
                "text": body_text,
                "content": body_text,
            }
            return extract_otp(item)
        except Exception:
            return None

    def _fetch_otp(self, conn: imaplib.IMAP4_SSL, uid: int) -> str | None:
        """按 UID 拉邮件正文并提取 OTP。构造与 extract_otp 兼容的 dict。"""
        typ, msg_data = conn.fetch(str(uid), "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)] BODY.PEEK[TEXT])")
        if typ != "OK":
            return None
        raw = b""
        header = b""
        for part in msg_data:
            if isinstance(part, tuple):
                block = part[1]
                # 区分 header 与 body 块：header 以 "Subject:" 等开头
                if b"Subject:" in block[:200] or b"From:" in block[:200]:
                    header += block
                else:
                    raw += block
        msg = email.message_from_bytes(header or b"")
        # 从正文提纯文本
        body_text = ""
        try:
            m2 = email.message_from_bytes(raw)
            for part in m2.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    body_text = payload.decode("utf-8", "replace")
                    break
            if not body_text:
                body_text = raw.decode("utf-8", "replace")
        except Exception:
            body_text = raw.decode("utf-8", "replace")
        item = {
            "subject": str(msg.get("Subject") or ""),
            "from": str(msg.get("From") or ""),
            "text": body_text,
            "content": body_text,
        }
        otp = extract_otp(item)
        return otp

    def wait_for_otp(
        self,
        after_ts: float | None = None,
        timeout: int = 90,
        interval: int = 1,
        settle_seconds: int = 1,
        exclude_codes: set[str] | None = None,
        on_poll: Callable[[dict], None] | None = None,
    ) -> str:
        exclude = set(str(c) for c in (exclude_codes or set()))
        deadline = time.time() + timeout
        t_start = time.time()
        conn = None
        # 首连(失败则重连)；初始无需记录 uid，靠 after_ts 时间过滤判新
        for _ in range(3):
            try:
                conn = self._ensure_conn()
                break
            except Exception as exc:
                logger.warning("[IMAP] 初始连接失败: %s，重连", exc)
                self.close()
                time.sleep(1)
        if conn is None:
            # 该邮箱 IMAP 不可用(缺 scope/未开 IMAP)，降级 Graph 收码
            logger.warning("[IMAP] %s 连接失败，降级 Graph 收码", self.email)
            return self._graph_fallback().wait_for_otp(
                after_ts=after_ts, timeout=timeout, interval=interval,
                settle_seconds=settle_seconds, exclude_codes=exclude_codes,
                on_poll=on_poll,
            )
        reported: set[tuple[str, bool]] = set()
        mid_warned = False
        conn_fails = 0

        while time.time() < deadline:
            try:
                conn = self._ensure_conn()
                hit = self._latest_since(conn, after_ts or 0.0)
                conn_fails = 0
            except Exception as exc:
                conn_fails += 1
                logger.warning("[IMAP] 拉取异常 %s（连续 %s 次），重连", exc, conn_fails)
                self.close()
                if conn_fails >= 3:
                    # IMAP 持续不可用，降级 Graph
                    logger.warning("[IMAP] %s 持续失败，降级 Graph 收码", self.email)
                    return self._graph_fallback().wait_for_otp(
                        after_ts=after_ts, timeout=timeout, interval=interval,
                        settle_seconds=settle_seconds, exclude_codes=exclude_codes,
                        on_poll=on_poll,
                    )
                time.sleep(interval)
                continue
            if hit:
                otp, uid, _ts = hit
                excluded = otp in exclude
                marker = (otp, excluded)
                if on_poll and marker not in reported:
                    reported.add(marker)
                    try:
                        on_poll({"code": otp, "excluded": excluded, "source": "imap",
                                 "elapsed_s": round(time.time() - t_start, 1)})
                    except Exception:
                        pass
                # 基于 after_ts 时间过滤已保证是本次新邮件，直接采用。
                # 不做 exclude 过滤(同主号 alias 可能收到相同码，exclude 会误伤死等)。
                delay = time.time() - t_start
                logger.info(
                    "[IMAP] 到件 OTP=%s uid=%s 延迟 %.1fs（email=%s）",
                    otp, uid, delay, self.email,
                )
                # settle 确认稳定
                st = time.time() + settle_seconds
                while time.time() < st:
                    time.sleep(0.2)
                return otp
            else:
                # 防止静默超时：过一半时长仍无新邮件时提示一次
                if not mid_warned and time.time() - t_start > timeout / 2:
                    mid_warned = True
                    logger.info(
                        "[IMAP] 等待 %s OTP 已 %.0fs 仍无新邮件",
                        self.email, time.time() - t_start,
                    )
            time.sleep(interval)
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
) -> MSMailClient | GmailApiClient | IMAPOAuthClient:
    mail_type = account.get("mail_type") or "ms_oauth"
    if mail_type == "gmail_api":
        return GmailApiClient(account, proxy=proxy, impersonate=impersonate)
    # ms_oauth 走 IMAP(XOAUTH2)，绕开 Graph ~150s 索引延迟(实测稳定 0.6s)
    return IMAPOAuthClient(account, proxy=proxy, impersonate=impersonate)


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

        # IMAP RFC822: "Wed, 05 Aug 2026 21:11:51 +0000 (UTC)"——剥掉 (UTC) 尾缀再解析
        clean = text.split(" (")[0].strip()
        return datetime.strptime(clean, "%a, %d %b %Y %H:%M:%S %z").timestamp()
    except Exception:
        return 0.0

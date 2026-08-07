"""本地 IMAP 收码（XOAUTH2 经 chain_via 隧道, 秒级到件）。"""
from __future__ import annotations

import base64
import email
import imaplib
import logging
import re
import socket
import ssl
import time
from typing import Any, Callable
from urllib.parse import urlparse

from curl_cffi import requests as cr

from gptreg.mail.base import MailClient
from gptreg.mail.ms_graph import MSMailClient
from gptreg.mail.otp_cache import (IMAP_OPENAI_SENDER, IMAP_SCOPE, IMAP_TOKEN_ENDPOINT,
                                   MailClientError, _parse_ts)
from gptreg.otp import extract_otp

logger = logging.getLogger(__name__)

class _ManualImap:
    """手动 IMAP 协议(经 chain_via 隧道)。

    本机 Python 的 imaplib 是旧版(无 sock 参数, 无法注入已 CONNECT 的 socket),
    故手动实现 XOAUTH2 / SELECT / SEARCH / FETCH(含 literal 解析)。
    连接走 chain_via CONNECT 隧道 → 海外出口, 解决本地中国 IP 直连部分账号被 MS 拒。
    """

    def __init__(self, sock: socket.socket, host: str):
        self.sock = sock
        self.file = sock.makefile("rb")
        self.tagnum = 0
        self._read_greeting()

    def _readline(self) -> str:
        line = self.file.readline()
        if not line:
            raise MailClientError("IMAP 连接已关闭")
        return line.decode("utf-8", "replace").rstrip("\r\n")

    def _read_greeting(self) -> None:
        for _ in range(10):
            line = self._readline()
            if line.startswith("* OK"):
                return
            if line.startswith("* BYE") or line.startswith("* NO"):
                raise MailClientError(f"IMAP greeting 异常: {line[:80]}")
        raise MailClientError("IMAP greeting 超时")

    def _tagged(self, command: str) -> tuple[str, list[tuple[str, bytes | None]]]:
        self.tagnum += 1
        tag = "A%04d" % self.tagnum
        self.sock.sendall(f"{tag} {command}\r\n".encode())
        untagged: list[tuple[str, bytes | None]] = []
        while True:
            line = self._readline()
            if line.startswith(tag + " "):
                return line, untagged
            # 处理行内/连续 literal。Outlook IMAP 的 FETCH 响应 literal 后**用空格分隔**
            # (非标准 \r\n), 之前 read(2) 吃掉 ' B' 导致下一 literal 行缺字节 → header/body 错位。
            # 改用 readline 读下一行并 strip 前导空白, 支持同一响应里多个 literal。
            while True:
                m = re.search(r"\{(\d+)\}$", line)
                if not m:
                    break
                size = int(m.group(1))
                literal = self.file.read(size)
                untagged.append((line, literal))
                line = self._readline().lstrip("\r\n ")
            untagged.append((line, None))

    def xoauth2(self, auth_str: str) -> tuple[str, list]:
        b64 = base64.b64encode(auth_str.encode("utf-8")).decode()
        return self._tagged(f"AUTHENTICATE XOAUTH2 {b64}")

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list]:
        return self._tagged(f'SELECT "{mailbox}"')

    def search_from(self, sender: str) -> tuple[str, list]:
        return self._tagged(f'SEARCH FROM "{sender}"')

    def fetch(self, num: str, parts: str) -> tuple[str, list]:
        return self._tagged(f"FETCH {num} {parts}")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

class IMAPOAuthClient(MailClient):
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
        cfg: dict[str, Any] | None = None,
    ):
        self.email = account["email"]
        self.client_id = account["client_id"]
        self.refresh_token = account["refresh_token"]
        self.proxy = proxy or None
        self.impersonate = impersonate
        self.timeout = timeout
        self._cfg = cfg or {}
        self._conn: imaplib.IMAP4_SSL | None = None
        self._access_token: str | None = None
        self._access_token_exp: float = 0.0
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
        # 缓存带过期校验(expires_in), 过期重换; MS 若轮换 refresh token 则更新 self.refresh_token
        # (否则降级 Graph 用旧 rt 会 invalid_grant, 长驻 client 用 stale token 持续失败)
        if not force and self._access_token and time.time() < self._access_token_exp - 60:
            return self._access_token
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": IMAP_SCOPE,
        }
        # token 交换用 MS 官方端点 + IMAP scope(缺 scope → token 无 IMAP 权限
        # → "authenticated but not connected")。直连不走注册代理。
        for proxies in (None, self._proxies() if self.proxy else None):
            try:
                r = cr.post(
                    IMAP_TOKEN_ENDPOINT,
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
                    self._access_token_exp = time.time() + int(j.get("expires_in") or 3600)
                    new_rt = j.get("refresh_token")
                    if new_rt:
                        self.refresh_token = new_rt
                    return at
                logger.warning("[IMAP] token 交换 status=%s: %s", r.status_code, str(j)[:150])
            except Exception as exc:
                logger.warning("[IMAP] token 请求失败(proxies=%s): %s", proxies, exc)
        return None

    def _proxies(self) -> dict | None:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def connect(self) -> "_ManualImap":
        at = self._get_access_token()
        if not at:
            raise MailClientError(f"IMAP 换 access_token 失败: {self.email}")
        conn = self._connect_imap()
        # XOAUTH2 的 user= 必须用登录基础邮箱(去 +tag 别名), 别名会导致认证异常
        login = self.email.split("+")[0] + "@" + self.email.split("@")[1] if "+" in self.email else self.email
        auth_str = f"user={login}\x01auth=Bearer {at}\x01\x01"
        try:
            status, _ = conn.xoauth2(auth_str)
        except Exception:
            conn.close()  # xoauth2 抛异常(socket 错)时也要关连接, 否则泄漏
            raise
        parts = status.split(" ", 2)
        if not (len(parts) >= 2 and parts[1] == "OK"):
            conn.close()
            raise MailClientError(f"IMAP XOAUTH2 认证失败: {status[:100]}")
        self._conn = conn
        return conn

    def _connect_imap(self) -> imaplib.IMAP4_SSL:
        """连 IMAP。优先经 chain_via(7890) CONNECT 隧道——本地中国 IP 直连对部分账号
        被 MS 拒(authenticated but not connected), 7890 出口(海外)能连。"""
        from urllib.parse import urlparse

        # 优先调用方 cfg(隧道出口与注册一致, 不重读磁盘); 无则 load_config() 兜底
        chain_via = ((self._cfg.get("proxy") or {}).get("dynamic") or {}).get("chain_via") or ""
        if not chain_via:
            try:
                from gptreg.config import load_config
                chain_via = ((load_config().get("proxy") or {}).get("dynamic") or {}).get("chain_via") or ""
            except Exception:
                chain_via = ""
        ctx = ssl.create_default_context()
        if not chain_via:
            raw = socket.create_connection((self.IMAP_HOST, self.IMAP_PORT), timeout=20)
            try:
                tls_sock = ctx.wrap_socket(raw, server_hostname=self.IMAP_HOST)
            except Exception:
                raw.close()
                raise
            return _ManualImap(tls_sock, self.IMAP_HOST)
        # 手动 CONNECT 隧道经 chain_via(实测可连 MS IMAP; PySocks 的 HTTP 隧道对 993 透传不稳)
        u = urlparse(chain_via if "://" in chain_via else "http://" + chain_via)
        sock = socket.create_connection((u.hostname, u.port or 80), timeout=20)
        req = f"CONNECT {self.IMAP_HOST}:{self.IMAP_PORT} HTTP/1.1\r\nHost: {self.IMAP_HOST}:{self.IMAP_PORT}\r\n"
        if u.username:
            b64 = base64.b64encode(f"{u.username}:{u.password or ''}".encode()).decode()
            req += f"Proxy-Authorization: Basic {b64}\r\n"
        req += "\r\n"
        sock.sendall(req.encode())
        sock.settimeout(20)
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp += chunk
        if b" 200 " not in resp.split(b"\r\n", 1)[0]:
            sock.close()
            raise MailClientError(f"chain_via CONNECT {self.IMAP_HOST}:{self.IMAP_PORT} 失败: {resp[:80]}")
        try:
            tls_sock = ctx.wrap_socket(sock, server_hostname=self.IMAP_HOST)
        except Exception:
            sock.close()
            raise
        return _ManualImap(tls_sock, self.IMAP_HOST)

    def _ensure_conn(self) -> imaplib.IMAP4_SSL:
        if self._conn is None:
            return self.connect()
        try:
            status, _ = self._conn.select("INBOX")
            parts = status.split(" ", 2)
            if not (len(parts) >= 2 and parts[1] == "OK"):
                self.close()
                return self.connect()
        except Exception:
            self.close()
            return self.connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _latest_since(self, conn: "_ManualImap", after_ts: float) -> tuple[str, int, float] | None:
        """返回最新且 Date>=after_ts 的 OpenAI 邮件 (otp, uid, date_ts)。无则 None。

        相比 UID 增量：send_otp 后邮件可能已到（初始 last_uid 已含目标邮件），
        UID 增量会漏检；用 after_ts 时间过滤能覆盖"发码前已到"的场景。
        手动 IMAP: SEARCH FROM + FETCH(header/text literal) 解析。
        """
        conn.select("INBOX")
        status, untagged = conn.search_from(IMAP_OPENAI_SENDER)
        ids: list[int] = []
        for line, _ in untagged:
            if line.startswith("* SEARCH"):
                ids = [int(x) for x in line.split()[2:]]
        if not ids:
            return None
        logger.debug("[IMAP/diag] _latest_since after_ts=%s ids=%s", after_ts, ids[-3:])
        # 从最新往前找，最多看最近 10 封
        for uid in reversed(ids[-10:]):
            try:
                status, ut = conn.fetch(str(uid), "(BODY.PEEK[HEADER.FIELDS (DATE SUBJECT)] BODY.PEEK[TEXT])")
                # _tagged 返回完整 tag 行如 "A0001 OK FETCH completed", endswith(" OK") 恒 False →
                # 本地 IMAP 收码曾 100% 静默超时。正确判断: 第二段 == "OK"
                if not (len(status.split(" ", 2)) >= 2 and status.split(" ", 2)[1] == "OK"):
                    continue
                header = b""
                body = b""
                for line, literal in ut:
                    if literal:
                        if "BODY[HEADER" in line:
                            header = literal
                        elif "BODY[TEXT" in line:
                            body = literal
                if not header:
                    continue
                msg = email.message_from_bytes(header)
                date_raw = str(msg.get("Date") or "")
                ts = _parse_ts(date_raw)
                logger.debug("[IMAP/diag] uid=%s date=%s ts=%s", uid, date_raw[:30], ts)
                if after_ts and ts and ts < after_ts:
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
                after_ts=after_ts, timeout=timeout,
                # Graph 索引延迟 150s+, 1s 轮询无意义, 降级至少 3s
                interval=max(3, interval),
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
                        after_ts=after_ts, timeout=timeout,
                        # Graph 索引延迟 150s+, 1s 轮询无意义, 降级至少 3s
                        interval=max(3, interval),
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

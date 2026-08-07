"""邮箱 OTP 公共: 已用码缓存 / 身份键 / 时间解析 / 异常 / 端点常量。"""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TOKEN_ENDPOINT = "https://login.live.com/oauth20_token.srf"
MAIL_ENDPOINT = "https://outlook.office.com/api/v2.0/me/messages"
IMAP_TOKEN_ENDPOINT = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
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

def _parse_ts(raw: str) -> float:
    if not raw:
        return 0.0
    text = str(raw).strip()
    try:
        if text.endswith("Z") or "T" in text:
            base = text.replace("Z", "")[:19]
            return float(calendar.timegm(time.strptime(base, "%Y-%m-%dT%H:%M:%S")))
        if " " in text:
            # 带时区偏移 "YYYY-MM-DD HH:MM:SS ±ZZZZ"(XDAuv sent_at 可能) → 转 UTC;
            # 否则按 UTC 解释。曾把带偏移的按 UTC 解析, 偏差数小时 → after_ts 过滤判错新旧。
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*([+-]\d{4})?$", text)
            if m and m.group(2):
                from datetime import datetime
                return datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z").timestamp()
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

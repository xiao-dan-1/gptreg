"""邮箱号池：线程安全 claim / mark，状态持久化到 .state.json。"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


def parse_mail_line(line: str) -> dict[str, Any] | None:
    """解析号池行。

    支持:
      email----password----client_id----refresh_token  -> ms_oauth
      email----https://...get-code...                 -> gmail_api
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split("----")
    if len(parts) == 2:
        email, code_url = parts[0].strip(), parts[1].strip()
        if not email or "@" not in email:
            return None
        if not code_url.startswith(("http://", "https://")):
            return None
        return {
            "email": email,
            "password": "",
            "client_id": "",
            "refresh_token": "",
            "code_url": code_url,
            "mail_type": "gmail_api",
            "raw_line": line,
        }
    if len(parts) < 4:
        return None
    email, password, client_id, refresh_token = (p.strip() for p in parts[:4])
    if not email or "@" not in email or not refresh_token:
        return None
    refresh_token = refresh_token.rstrip("$").rstrip()
    return {
        "email": email,
        "password": password,
        "client_id": client_id,
        "refresh_token": refresh_token,
        "code_url": "",
        "mail_type": "ms_oauth",
        "raw_line": line,
    }


def choose_registration_email(account: dict[str, Any], cfg: dict[str, Any] | None = None) -> tuple[str, bool]:
    """返回 (注册邮箱, 是否使用别名)。

    对齐 k12:
      - use_alias=false: 原样使用号池邮箱
      - use_alias=true:  从主号 local 生成 local+tag@domain
      - 号池已是 manual alias (含 +) 时，先剥掉 +tag 再生成，避免 double alias
    收码仍用号池 OAuth 主凭据（MSMailClient.email 保持号池行）。
    """
    import secrets

    mail_cfg = (cfg or {}).get("mail", {}) if cfg else {}
    raw = (account.get("email") or "").strip()
    if not raw or "@" not in raw:
        return raw, False
    use_alias = bool(mail_cfg.get("use_alias", False))
    if not use_alias:
        return raw, False
    local, domain = raw.rsplit("@", 1)
    base_local = local.split("+", 1)[0]
    tag_len = max(3, int(mail_cfg.get("alias_tag_len", 6) or 6))
    # 偶数 hex 长度
    if tag_len % 2:
        tag_len += 1
    tag = secrets.token_hex(tag_len // 2)
    reg = f"{base_local}+{tag}@{domain}"
    return reg, True


class MailPool:
    """文件号池 + 状态文件。"""

    def __init__(self, pool_file: str | Path):
        self.pool_file = Path(pool_file).resolve()
        self.state_file = Path(str(self.pool_file) + ".state.json")
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._by_email: dict[str, dict[str, Any]] = {}
        self._used: set[str] = set()
        self._bad: set[str] = set()
        self._in_flight: set[str] = set()
        self._failed: dict[str, int] = {}

    def load(self) -> int:
        if not self.pool_file.exists():
            raise FileNotFoundError(f"邮箱号池不存在: {self.pool_file}")
        records: list[dict[str, Any]] = []
        by_email: dict[str, dict[str, Any]] = {}
        for raw in self.pool_file.read_text(encoding="utf-8").splitlines():
            rec = parse_mail_line(raw)
            if not rec:
                continue
            email = rec["email"]
            if email in by_email:
                continue
            records.append(rec)
            by_email[email] = rec
        with self._lock:
            self._records = records
            self._by_email = by_email
            self._used = set()
            self._bad = set()
            self._in_flight = set()
            self._failed = {}
        self._load_state()
        return len(records)

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        with self._lock:
            self._used = set(data.get("used") or [])
            self._bad = set(data.get("bad") or [])
            self._failed = dict(data.get("failed") or {})

    def _save_state(self) -> None:
        payload = {
            "used": sorted(self._used),
            "bad": sorted(self._bad),
            "failed": dict(self._failed),
            "saved_at": int(time.time()),
        }
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

    def claim(self) -> dict[str, Any] | None:
        with self._lock:
            for rec in self._records:
                email = rec["email"]
                if email in self._used or email in self._bad or email in self._in_flight:
                    continue
                if email in self._failed:
                    continue
                self._in_flight.add(email)
                return dict(rec)
            for rec in self._records:
                email = rec["email"]
                if email in self._used or email in self._bad or email in self._in_flight:
                    continue
                if self._failed.get(email, 0) < 3:
                    self._in_flight.add(email)
                    return dict(rec)
        return None

    def release(self, email: str) -> None:
        with self._lock:
            self._in_flight.discard(email)

    def mark_used(self, email: str) -> None:
        with self._lock:
            self._used.add(email)
            self._failed.pop(email, None)
            self._bad.discard(email)
            self._in_flight.discard(email)
        self._save_state()

    def mark_bad(self, email: str, reason: str = "") -> None:
        with self._lock:
            self._bad.add(email)
            self._in_flight.discard(email)
        self._save_state()

    def mark_failed(self, email: str, max_retry: int = 3) -> None:
        with self._lock:
            n = self._failed.get(email, 0) + 1
            self._failed[email] = n
            self._in_flight.discard(email)
            if n >= max_retry:
                self._bad.add(email)
        self._save_state()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._records)
            used = len(self._used)
            bad = len(self._bad)
            inflight = len(self._in_flight)
            retrying = len(
                [e for e in self._failed if e not in self._used and e not in self._bad]
            )
            unused = max(0, total - used - bad - inflight - retrying)
            return {
                "total": total,
                "unused": unused,
                "used": used,
                "bad": bad,
                "in_flight": inflight,
                "retrying": retrying,
                "pool_file": str(self.pool_file),
            }

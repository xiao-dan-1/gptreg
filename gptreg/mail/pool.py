"""邮箱号池：线程安全 claim / mark，状态持久化到 .state.json。"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


# 失败/弃用 TTL(秒): 瞬时基建故障(代理/网络)后账号自动回退, 避免误烧永久弃用
FAILED_TTL = 1800  # 基建/可重试失败, 30 分钟后自动回退(代理恢复账号复活)
BAD_TTL = 86400  # 账号级弃用, 24h 后自动回退(风控可能解除)


def parse_mail_line(line: str) -> dict[str, Any] | None:
    """解析号池行(遍历来源插件 MAIL_SOURCES 识别)。

    支持(由 sources.MAIL_SOURCES 各插件识别):
      email----password----client_id----refresh_token  -> ms_oauth
      email----https://...get-code...                 -> icloud(@icloud.com/@me.com)
    新增号源只需注册进 sources.MAIL_SOURCES, 本函数零改动。
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    from gptreg.mail.sources import MAIL_SOURCES

    for src in MAIL_SOURCES.values():
        acc = src.parse_line(line)
        if acc:
            return acc
    return None


def accounts_registered_mains(accounts_jsonl: str | Path) -> set[str]:
    """从 accounts.jsonl 反查"已注册主号"集合。

    注册用 plus 别名(x+tag@dom), 主号是 x@dom。alias 剥 tag 后并入 used,
    号池与账号表联动: 已注册过的主号不再被 claim(避免 create 400 邮箱已存在)。
    """
    p = Path(accounts_jsonl)
    used: set[str] = set()
    if not p.exists():
        return used
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        email = (d.get("email") or "").strip()
        if not email or "@" not in email:
            continue
        local, dom = email.rsplit("@", 1)
        used.add(f"{local.split('+')[0]}@{dom}")
    return used


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

    def __init__(self, pool_file: str | Path, accounts_jsonl: str | Path | None = None):
        self.pool_file = Path(pool_file).resolve()
        self.state_file = Path(str(self.pool_file) + ".state.json")
        # 账号主库: 已注册主号反查源(号池与账号表联动, 避免重复用已注册主号)
        self.accounts_jsonl = Path(accounts_jsonl) if accounts_jsonl else None
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._by_email: dict[str, dict[str, Any]] = {}
        self._used: set[str] = set()
        self._bad: dict[str, float] = {}  # email → 弃用时间戳(TTL 后自动回退)
        self._in_flight: set[str] = set()
        self._failed: dict[str, dict] = {}  # email → {n, ts}(TTL 后自动回退)

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
            self._bad = {}
            self._in_flight = set()
            self._failed = {}
        self._load_state()
        # 号池与账号表联动: 已注册主号(accounts.jsonl 反查) 并入 used
        self._sync_registered()
        return len(records)

    def _sync_registered(self) -> None:
        """从 accounts.jsonl 反查已注册主号并入 _used(号池 state 与账号表不脱节)。

        load() 后调用(锁外拿反查结果, 锁内并入), 避免重复用已注册主号导致 create 400。
        """
        if not self.accounts_jsonl:
            return
        registered = accounts_registered_mains(self.accounts_jsonl)
        if not registered:
            return
        dirty = False
        with self._lock:
            for main in registered:
                if main not in self._used:
                    self._used.add(main)
                    dirty = True
        # _save_state 内部自带 _lock, 须锁外调用(避免 Lock 死锁)
        if dirty:
            self._save_state()

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        with self._lock:
            self._used = set(data.get("used") or [])
            # bad 兼容旧格式(set/list) → dict[email, ts]
            raw_bad = data.get("bad") or {}
            if isinstance(raw_bad, dict):
                self._bad = {
                    k: float(v.get("ts", time.time())) if isinstance(v, dict) else float(v or time.time())
                    for k, v in raw_bad.items()
                }
            else:
                self._bad = {k: time.time() for k in raw_bad}
            # failed 兼容旧格式({email:int}) → {email:{n,ts}}
            raw_failed = data.get("failed") or {}
            self._failed = {}
            for k, v in raw_failed.items():
                if isinstance(v, dict):
                    self._failed[k] = {"n": int(v.get("n", 1)), "ts": float(v.get("ts", time.time()))}
                else:
                    self._failed[k] = {"n": int(v or 1), "ts": time.time()}

    def _save_state(self) -> None:
        # 锁内快照+写临时文件: 并发 workers 锁外迭代 set/dict 曾抛
        # RuntimeError(Set changed size)或共写同一 .tmp 损坏 state.json → 已用邮箱状态丢失、
        # 下次重复 claim 产出重复账号。持久化必须整体持锁。
        with self._lock:
            payload = {
                "used": sorted(self._used),
                "bad": {k: round(ts, 2) for k, ts in self._bad.items()},
                "failed": {
                    k: {"n": v["n"], "ts": round(v["ts"], 2)} for k, v in self._failed.items()
                },
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
            dirty = self._purge_expired()  # TTL 过期自动回退, 账号复活
            _ret: dict[str, Any] | None = None
            for rec in self._records:
                email = rec["email"]
                if email in self._used or email in self._bad or email in self._in_flight:
                    continue
                if email in self._failed:
                    continue
                self._in_flight.add(email)
                _ret = dict(rec)
                break
            if _ret is None:
                for rec in self._records:
                    email = rec["email"]
                    if email in self._used or email in self._bad or email in self._in_flight:
                        continue
                    if self._failed.get(email, {}).get("n", 0) < 3:
                        self._in_flight.add(email)
                        _ret = dict(rec)
                        break
        if dirty:
            self._save_state()
        return _ret

    def release(self, email: str) -> None:
        with self._lock:
            self._in_flight.discard(email)

    def mark_used(self, email: str) -> None:
        with self._lock:
            self._used.add(email)
            self._failed.pop(email, None)
            self._bad.pop(email, None)
            self._in_flight.discard(email)
        self._save_state()

    def mark_bad(self, email: str, reason: str = "") -> None:
        with self._lock:
            self._bad[email] = time.time()  # 弃用时间戳(BAD_TTL 后自动回退)
            self._in_flight.discard(email)
        self._save_state()

    def mark_failed(self, email: str, max_retry: int = 3) -> None:
        with self._lock:
            cur = self._failed.get(email, {})
            n = int(cur.get("n", 0)) + 1
            # 基建/可重试失败: 记计数+时间戳, 不自动永久弃用(账号级由 mark_bad 显式标记)。
            # TTL 过期自动回退(代理/网络恢复账号复活), 修复基建故障误烧整池账号。
            self._failed[email] = {"n": n, "ts": time.time()}
            self._in_flight.discard(email)
        self._save_state()

    def _purge_expired(self) -> bool:
        """锁内调用: TTL 过期清理, 返回是否变更(调用方锁外 _save_state, 避免 Lock 死锁)。"""
        now = time.time()
        dirty = False
        for k in [k for k, ts in self._bad.items() if now - ts > BAD_TTL]:
            self._bad.pop(k, None)
            dirty = True
        for k in [k for k, v in self._failed.items() if now - v.get("ts", 0) > FAILED_TTL]:
            self._failed.pop(k, None)
            dirty = True
        return dirty

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

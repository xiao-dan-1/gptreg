"""成功账号落盘。"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from gptreg.config import resolve_path

_LOCK = threading.RLock()


def ensure_output_dir(cfg: dict[str, Any]) -> Path:
    out = resolve_path(cfg.get("output", {}).get("dir", "output"), Path(cfg["_root"]))
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_success(
    cfg: dict[str, Any],
    *,
    email: str,
    access_token: str,
    account: dict[str, Any],
    session_info: dict[str, Any],
    proxy_used: str,
    device_id: str,
    name: str,
    birthdate: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    out_dir = ensure_output_dir(cfg)
    output = cfg.get("output", {})
    material = account.get("raw_line") or email
    copy_line = f"{material}----{access_token}"
    record = {
        "email": email,
        "access_token": access_token,
        "mail_type": account.get("mail_type"),
        "material_line": material,
        "copy_line": copy_line,
        "proxy_used": proxy_used,
        "device_id": device_id,
        "name": name,
        "birthdate": birthdate,
        "user": session_info.get("user"),
        "account": session_info.get("account"),
        "expires": session_info.get("expires"),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    if extra:
        # 观测字段（sentinel_obs/health 等）；不覆盖核心键
        for k, v in extra.items():
            if k not in record:
                record[k] = v

    accounts_path = out_dir / output.get("accounts_jsonl", "accounts.jsonl")
    tokens_path = out_dir / output.get("tokens_txt", "tokens.txt")
    emails_path = out_dir / output.get("emails_txt", "emails.txt")
    full_path = out_dir / output.get("full_lines_txt", "full_lines.txt")

    with _LOCK:
        with accounts_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        with tokens_path.open("a", encoding="utf-8") as f:
            f.write(access_token + "\n")
        with emails_path.open("a", encoding="utf-8") as f:
            f.write(material + "\n")
        with full_path.open("a", encoding="utf-8") as f:
            f.write(copy_line + "\n")
    return out_dir

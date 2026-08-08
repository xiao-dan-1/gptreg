#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探测干净主号(accounts.jsonl 无痕迹)的 IMAP 可用性, 挑可用的收码。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.mail.pool import parse_mail_line
from gptreg.mail.providers import build_mail_client

# 1. accounts.jsonl 痕迹 base
used_base = set()
p = Path(ROOT) / "output" / "accounts.jsonl"
if p.exists():
    for l in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(l)
            e = (d.get("email") or "").lower()
            used_base.add(e.split("@")[0].split("+")[0])
        except Exception:
            pass

# 2. 号池干净可用
state = json.loads((Path(ROOT) / "mail_pool.txt.state.json").read_text(encoding="utf-8"))
used_state = set(state.get("used", []))
bad = state.get("bad", {})
clean = []
for line in (Path(ROOT) / "mail_pool.txt").read_text(encoding="utf-8").splitlines():
    a = parse_mail_line(line.strip())
    if not a:
        continue
    email = a["email"]
    base = email.split("@")[0].split("+")[0].lower()
    if email in used_state or email in bad or base in used_base:
        continue
    clean.append(a)

print(f"干净可用主号: {len(clean)} 个, 测前 {min(10, len(clean))} 个 IMAP:")
ok = 0
for i, a in enumerate(clean[:10], 1):
    try:
        client = build_mail_client(a)
        cls = type(client).__name__
        if cls == "IMAPOAuthClient":
            t0 = time.time()
            try:
                conn = client.connect()
                conn.select("INBOX", readonly=True)
                client.close()
                print(f"  [{i}] {a['email']:42s} ✅ IMAP 可用 ({(time.time()-t0):.1f}s)")
                ok += 1
            except Exception as exc:
                print(f"  [{i}] {a['email']:42s} ❌ {str(exc)[:60]}")
        else:
            print(f"  [{i}] {a['email']:42s} (通道 {cls})")
    except Exception as exc:
        print(f"  [{i}] {a['email']:42s} 构建失败: {str(exc)[:60]}")

print(f"\n可用 {ok}/{min(10, len(clean))}")

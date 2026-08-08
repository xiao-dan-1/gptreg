#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探测 iCloud 接码 HTML 收件箱: 解析卡片结构, 长轮询等待邮件。

目标: 摸清 icloud-api.top HTML 收件箱的稳定提取方式(验证码从 .bd 正文提)。
"""
from __future__ import annotations

import html
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from curl_cffi import requests as cr

URL = sys.argv[1] if len(sys.argv) > 1 else (
    "https://icloud-api.top/s/YrJz9CP1tL3hOIcYpU_K1JO8CWmQ5dmG/phony-records-47@icloud.com"
)
WAIT = float(sys.argv[2]) if len(sys.argv) > 2 else 180


def parse_inbox(body: str) -> dict:
    """解析 HTML 收件箱: {count, mails:[{subject,date,body_text,otp}]}"""
    m = re.search(r'class="cnt">(\d+)\s*封', body)
    count = int(m.group(1)) if m else 0
    mails = []
    cards = re.findall(r'class="card".*?(?=class="card"|</body>)', body, flags=re.S)
    for c in cards:
        su = re.search(r'class="su">(.*?)</div>', c, flags=re.S)
        dt = re.search(r'class="dt">(.*?)</div>', c, flags=re.S)
        bd = re.search(r'class="bd">(.*?)</div>\s*</div>', c, flags=re.S)
        fr = re.search(r'class="fr">(.*?)</div>', c, flags=re.S)
        subject = html.unescape(re.sub(r"<[^>]+>", "", su.group(1))).strip() if su else ""
        date = html.unescape(re.sub(r"<[^>]+>", "", dt.group(1))).strip() if dt else ""
        sender = html.unescape(re.sub(r"<[^>]+>", "", fr.group(1))).strip() if fr else ""
        body_html = bd.group(1) if bd else ""
        body_text = html.unescape(re.sub(r"<[^>]+>", " ", body_html))
        body_text = re.sub(r"\s+", " ", body_text).strip()
        otp = None
        mm = re.search(r"\b(\d{6})\b", body_text)
        if mm:
            otp = mm.group(1)
        mails.append({"sender": sender, "subject": subject, "date": date,
                      "body_text": body_text, "otp": otp})
    return {"count": count, "mails": mails}


def main() -> int:
    print(f"URL: {URL[:80]}... 等待最长 {WAIT:.0f}s")
    deadline = time.time() + WAIT
    t_start = time.time()
    seen_count = 0
    while time.time() < deadline:
        try:
            r = cr.get(URL, timeout=25, impersonate="chrome142")
            info = parse_inbox(r.text)
            if info["count"] > seen_count:
                seen_count = info["count"]
                print(f"\n[{(time.time()-t_start):.0f}s] 邮件到达: {info['count']} 封")
                for ml in info["mails"]:
                    print(f"  发件: {ml['sender'][:40]}")
                    print(f"  主题: {ml['subject'][:80]}")
                    print(f"  时间: {ml['date']}")
                    print(f"  正文: {ml['body_text'][:200]}")
                    print(f"  OTP: {ml['otp']}")
                print()
            else:
                print(f"[{(time.time()-t_start):.0f}s] 无新邮件 ({info['count']} 封)", end="\r")
        except Exception as exc:
            print(f"  请求异常: {type(exc).__name__}: {str(exc)[:80]}")
        time.sleep(5)
    print()
    print("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

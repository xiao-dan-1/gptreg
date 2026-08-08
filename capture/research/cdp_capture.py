#!/usr/bin/env python3
"""Connect to local Chrome via Playwright CDP and capture registration traffic.

Prereq: Chrome started with:
  chrome.exe --remote-debugging-port=9222 --remote-allow-origins=*
             --user-data-dir=... "https://chatgpt.com/auth/login"

Usage:
  /path/to/anaconda3/python.exe capture/cdp_capture.py [--port 9222]

Operate the Chrome window manually. Ctrl+C to stop and write:
  capture/session-*/events.jsonl
  capture/session-*/summary.txt
  capture/session-*/full.json
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

INTERESTING = re.compile(
    r"("
    r"auth\.openai\.com|"
    r"chatgpt\.com/(api/auth|backend-api|backend-anon)|"
    r"sentinel\.openai\.com|"
    r"accounts\.google\.com|"
    r"login\.live\.com|"
    r"login\.microsoftonline\.com"
    r")",
    re.I,
)

KEY_PATHS = (
    "create_account",
    "email-otp",
    "sentinel/req",
    "accounts/check",
    "/me",
    "session",
    "authorize",
    "callback",
    "user/register",
    "about-you",
    "signin",
    "password",
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_interesting(url: str) -> bool:
    return bool(INTERESTING.search(url or ""))


def want_body(url: str) -> bool:
    path = urlparse(url or "").path
    return any(k in path for k in KEY_PATHS)


def pick_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    # keep all non-cookie headers for protocol analysis; cookie keys only
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in ("cookie", "set-cookie"):
            keys = []
            for part in str(v).split(";"):
                part = part.strip()
                if part:
                    keys.append(part.split("=", 1)[0])
            out[k] = f"***COOKIE*** keys={','.join(keys[:40])} len={len(str(v))}"
        elif lk == "authorization":
            s = str(v)
            if s.lower().startswith("bearer "):
                tok = s[7:]
                out[k] = f"Bearer ***REDACTED*** len={len(tok)} prefix={tok[:16]}"
            else:
                out[k] = "***REDACTED***"
        else:
            # keep sentinel / device / ua full
            out[k] = str(v)
    return out


class CaptureSession:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = out_dir / "events.jsonl"
        self.summary_path = out_dir / "summary.txt"
        self.full_path = out_dir / "full.json"
        self._lock = threading.Lock()
        self._done: list[dict[str, Any]] = []
        self._count = 0
        self.events_path.write_text("", encoding="utf-8")

    def on_request(self, request) -> None:
        try:
            url = request.url
            if not is_interesting(url):
                return
            entry: dict[str, Any] = {
                "ts": now_iso(),
                "phase": "request",
                "method": request.method,
                "url": url,
                "resource_type": request.resource_type,
                "request_headers": pick_headers(request.headers),
                "post_data": None,
            }
            try:
                pd = request.post_data
                if pd is not None:
                    entry["post_data"] = pd
            except Exception:
                pass
            with self._lock:
                self._count += 1
                self._append(entry)
            path = urlparse(url).path
            print(f"[{now_iso()}] → {request.method} {urlparse(url).netloc}{path[:90]}", flush=True)
        except Exception as exc:
            print(f"[{now_iso()}] request handler error: {exc}", flush=True)

    def on_response(self, response) -> None:
        try:
            url = response.url
            if not is_interesting(url):
                return
            req = response.request
            entry: dict[str, Any] = {
                "ts": now_iso(),
                "phase": "response",
                "method": req.method,
                "url": url,
                "status": response.status,
                "request_headers": pick_headers(req.headers),
                "response_headers": pick_headers(response.headers),
                "post_data": None,
                "body": None,
            }
            try:
                pd = req.post_data
                if pd is not None:
                    entry["post_data"] = pd
            except Exception:
                pass
            if want_body(url):
                try:
                    # text body; may fail for binary
                    text = response.text()
                    if text is not None:
                        # cap very large bodies
                        if len(text) > 200_000:
                            entry["body"] = text[:200_000] + f"...TRUNC len={len(text)}"
                        else:
                            entry["body"] = text
                except Exception as exc:
                    entry["body_error"] = str(exc)
            with self._lock:
                self._append(entry)
            path = urlparse(url).path
            print(f"[{now_iso()}] ← {response.status} {req.method} {path[:90]}", flush=True)
        except Exception as exc:
            print(f"[{now_iso()}] response handler error: {exc}", flush=True)

    def _append(self, entry: dict[str, Any]) -> None:
        self._done.append(entry)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def write_summary(self) -> None:
        with self._lock:
            rows = list(self._done)
        self.full_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        lines: list[str] = []
        lines.append(f"capture_end: {now_iso()}")
        lines.append(f"events: {len(rows)}")
        lines.append("")
        for e in rows:
            if e.get("phase") != "response" and e.get("status") is None:
                # still show request-only lines briefly
                path = urlparse(e.get("url") or "").path
                lines.append(f"{e.get('ts')}  {e.get('method')}  (req)  {path}")
                continue
            path = urlparse(e.get("url") or "").path
            lines.append(f"{e.get('ts')}  {e.get('method')} {e.get('status')}  {path}")
            rh = e.get("request_headers") or {}
            for hk in (
                "openai-sentinel-token",
                "openai-sentinel-so-token",
                "oai-device-id",
                "oai-language",
                "user-agent",
                "authorization",
                "content-type",
            ):
                val = None
                for k, v in rh.items():
                    if k.lower() == hk:
                        val = v
                        break
                if val is not None:
                    show = val if len(str(val)) < 140 else str(val)[:120] + f"... len={len(str(val))}"
                    lines.append(f"    H {hk}: {show}")
            if e.get("post_data"):
                pd = e["post_data"]
                show = pd if len(pd) < 240 else pd[:220] + f"... len={len(pd)}"
                lines.append(f"    BODY: {show}")
            if e.get("body") and want_body(e.get("url") or ""):
                b = e["body"]
                show = b if len(b) < 240 else b[:220] + f"... len={len(b)}"
                lines.append(f"    RESP: {show}")
            lines.append("")
        self.summary_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[{now_iso()}] wrote {self.summary_path}", flush=True)
        print(f"[{now_iso()}] wrote {self.full_path} ({len(rows)} entries)", flush=True)
        print(f"[{now_iso()}] wrote {self.events_path}", flush=True)


def attach_to_existing(page, sess: CaptureSession) -> None:
    page.on("request", sess.on_request)
    page.on("response", sess.on_response)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    out = Path(args.out) if args.out else Path("capture") / f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if not out.is_absolute():
        out = Path.cwd() / out

    endpoint = f"http://127.0.0.1:{args.port}"
    print(f"[{now_iso()}] connecting Playwright CDP → {endpoint}", flush=True)

    stop = threading.Event()

    def _sig(*_a):
        print(f"\n[{now_iso()}] stopping...", flush=True)
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sig)

    sess = CaptureSession(out)
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(endpoint)
        except Exception as exc:
            print(f"[{now_iso()}] connect failed: {exc}", flush=True)
            print("Make sure Chrome is running with --remote-debugging-port=9222", flush=True)
            return 1

        contexts = browser.contexts
        print(f"[{now_iso()}] contexts={len(contexts)}", flush=True)
        pages = []
        for ctx in contexts:
            for page in ctx.pages:
                pages.append(page)
                print(f"[{now_iso()}] page: {page.url}", flush=True)
                attach_to_existing(page, sess)

        # also capture new pages/popups
        for ctx in contexts:
            def _on_page(page, _sess=sess):
                print(f"[{now_iso()}] new page: {page.url}", flush=True)
                attach_to_existing(page, _sess)

            ctx.on("page", _on_page)

        meta = {
            "started": now_iso(),
            "endpoint": endpoint,
            "pages": [pg.url for pg in pages],
        }
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"[{now_iso()}] Network capture ON → {out}", flush=True)
        print("请在 Chrome 窗口里手动完成注册。完成后回到终端 Ctrl+C 结束抓包。", flush=True)
        print("（也可直接告诉我「抓完了」，我来停进程并分析）", flush=True)
        print("NOTE: Playwright sync 必须用 wait_for_timeout 泵事件；禁止 time.sleep。", flush=True)

        # CRITICAL: Playwright sync API does not deliver page.on('request') callbacks
        # while blocked in time.sleep(). Pump via wait_for_timeout on a live page.
        pump_page = pages[0] if pages else None
        while not stop.is_set():
            try:
                # refresh page list if user opened new tabs
                if not pump_page or pump_page.is_closed():
                    live_pages = [pg for ctx in browser.contexts for pg in ctx.pages if not pg.is_closed()]
                    pump_page = live_pages[0] if live_pages else None
                if pump_page is not None:
                    pump_page.wait_for_timeout(300)
                else:
                    time.sleep(0.3)
            except Exception as exc:
                # connection hiccup; brief backoff then retry until stop
                print(f"[{now_iso()}] pump warn: {exc}", flush=True)
                time.sleep(0.5)

        sess.write_summary()
        try:
            browser.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

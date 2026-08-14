# -*- coding: utf-8 -*-
"""本地 sentinel 中转(:1789)。node runner 把 sentinel/req 打到这里,
本进程用 curl_cffi(impersonate,过 CF)转发真 sentinel;出口可用 OAI_SENTINEL_EXIT
(或回落 REAUTH_HOP1)配置为跟随住宅代理。

node fetch 过不了 CF(undici 指纹 + 不支持 socks);curl_cffi 能过。
"""
from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from curl_cffi import requests as cr

SV = "20260219f9f6"
UPSTREAM = "https://sentinel.openai.com/backend-api/sentinel/req"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")


def _exit_proxy() -> str:
    raw = (os.environ.get("OAI_SENTINEL_EXIT", "") or os.environ.get("REAUTH_HOP1", "")).strip()
    return raw.replace("socks5://", "socks5h://", 1) if raw else ""


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(ln)
        proxy = _exit_proxy()
        try:
            kwargs = dict(
                headers={"Content-Type": "text/plain;charset=UTF-8",
                         "Origin": "https://sentinel.openai.com",
                         "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=" + SV,
                         "User-Agent": UA,
                         "sec-ch-ua": '"Chromium";v="146", "Google Chrome";v="146", "Not?A_Brand";v="99"',
                         "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"'},
                impersonate="chrome", timeout=30,
            )
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
            r = cr.post(UPSTREAM, data=body, **kwargs)
            data, code = r.content, r.status_code
        except Exception as e:
            data, code = ("proxy_err: " + str(e)).encode("utf-8"), 502
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # 就绪探测
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def serve(host: str = "127.0.0.1", port: int = 1789):
    via = ("via " + _exit_proxy()) if _exit_proxy() else "direct (no exit proxy)"
    print("sentinel proxy listening %s:%d -> %s [%s]" % (host, port, UPSTREAM, via))
    ThreadingHTTPServer((host, port), _Handler).serve_forever()


if __name__ == "__main__":
    serve()

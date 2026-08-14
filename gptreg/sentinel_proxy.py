"""本地 sentinel/req 中转（对齐 k12 `_sentinel_proxy.py`）。

Node runner 的 fetch 过不了 CF / 不好走 socks；由本进程用 curl_cffi(impersonate)
转发到真实 `sentinel.openai.com`，保证 SDK 内产生的 `p` 与 challenge 请求一致。
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_UPSTREAM = "https://sentinel.openai.com/backend-api/sentinel/req"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 1789

_lock = threading.Lock()
_state: dict[str, Any] = {
    "server": None,
    "thread": None,
    "port": None,
    "proxy": None,
    "sv": None,
    "ua": None,
    "impersonate": None,
}


def _pick_forward_proxy(cfg: dict[str, Any] | None) -> str | None:
    """中转出站：优先 chain_via(7890)，再 default，再环境常见本地代理。"""
    cfg = cfg or {}
    proxy_cfg = cfg.get("proxy") or {}
    dyn = proxy_cfg.get("dynamic") or {}
    for key in ("chain_via",):
        val = (dyn.get(key) or "").strip()
        if val:
            return val
    val = (proxy_cfg.get("default") or "").strip()
    if val:
        return val
    return "http://127.0.0.1:7890"


def _handler_factory(
    *,
    upstream: str,
    forward_proxy: str | None,
    sv: str,
    ua: str,
    impersonate: str,
):
    from curl_cffi import requests as cr

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            headers = {
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "https://sentinel.openai.com",
                "Referer": (
                    "https://sentinel.openai.com/backend-api/sentinel/frame.html"
                    f"?sv={sv}"
                ),
                "User-Agent": ua,
            }
            proxies = None
            if forward_proxy:
                proxies = {"http": forward_proxy, "https": forward_proxy}
            try:
                resp = cr.post(
                    upstream,
                    data=body,
                    headers=headers,
                    impersonate=impersonate,
                    timeout=30,
                    proxies=proxies,
                )
                data = resp.content
                code = int(resp.status_code)
            except Exception as exc:  # noqa: BLE001
                data = f"proxy_err: {exc}".encode("utf-8")
                code = 502
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):  # quiet
            return

    return Handler


def challenge_proxy_url(port: int | None = None) -> str:
    p = port or _state.get("port") or _DEFAULT_PORT
    return f"http://{_DEFAULT_HOST}:{p}/req"


def ensure_sentinel_proxy(cfg: dict[str, Any] | None = None) -> str:
    """确保本地中转已起；返回 challenge-url（http://127.0.0.1:port/req）。"""
    cfg = cfg or {}
    protocol = cfg.get("protocol") or {}
    browser = cfg.get("browser") or {}
    sv = str(protocol.get("sentinel_sv") or "20260219f9f6")
    ua = str(
        browser.get("user_agent")
        or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        )
    )
    impersonate = str(browser.get("impersonate") or "chrome142")
    forward = _pick_forward_proxy(cfg)
    port = int(protocol.get("sentinel_proxy_port") or _DEFAULT_PORT)

    with _lock:
        srv = _state.get("server")
        if srv is not None and _state.get("port") == port:
            # 配置变更时重建
            same = (
                _state.get("proxy") == forward
                and _state.get("sv") == sv
                and _state.get("ua") == ua
                and _state.get("impersonate") == impersonate
            )
            if same:
                return challenge_proxy_url(port)
            try:
                srv.shutdown()
            except Exception:  # noqa: BLE001
                pass
            _state["server"] = None
            _state["thread"] = None

        handler = _handler_factory(
            upstream=_UPSTREAM,
            forward_proxy=forward,
            sv=sv,
            ua=ua,
            impersonate=impersonate,
        )
        server = ThreadingHTTPServer((_DEFAULT_HOST, port), handler)
        thread = threading.Thread(target=server.serve_forever, name="sentinel-proxy", daemon=True)
        thread.start()
        _state.update(
            {
                "server": server,
                "thread": thread,
                "port": port,
                "proxy": forward,
                "sv": sv,
                "ua": ua,
                "impersonate": impersonate,
            }
        )
        logger.info(
            "[SentinelProxy] listen %s proxy=%s sv=%s",
            challenge_proxy_url(port),
            _redact_proxy(forward),
            sv,
        )
        return challenge_proxy_url(port)


def stop_sentinel_proxy() -> None:
    with _lock:
        srv = _state.get("server")
        if srv is None:
            return
        try:
            srv.shutdown()
        except Exception:  # noqa: BLE001
            pass
        _state["server"] = None
        _state["thread"] = None
        logger.info("[SentinelProxy] stopped")


def _redact_proxy(url: str | None) -> str:
    if not url:
        return "direct"
    try:
        p = urlparse(url)
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://{host}{port}"
    except Exception:  # noqa: BLE001
        return "proxy"

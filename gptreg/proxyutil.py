"""动态代理（辣椒 lajiao 等）与本地链式隧道。

格式示例:
  qqfow23217-region-US-sid-2As2LXe5-t-5:dve1rnfm@us.lajiaohttp.net:2000

- 改 region-XX 换地区
- 改 sid-XXXX 换 IP（同 sid 粘性约 t-N 分钟）
- 直连常 403 时，经 chain_via（如 127.0.0.1:10808）做 CONNECT 隧道
"""
from __future__ import annotations

import base64
import logging
import random
import re
import socket
import string
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SID_RE = re.compile(r"-sid-[^-@]+-t-")
_REGION_RE = re.compile(r"-region-[A-Za-z0-9]+-")
_SID_CHARS = string.ascii_letters + string.digits


def random_sid(length: int = 8) -> str:
    return "".join(random.choice(_SID_CHARS) for _ in range(max(4, int(length or 8))))


def ensure_http_proxy_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return text


def proxy_label(url: str) -> str:
    """脱敏展示，保留 region/sid/host。"""
    if not url:
        return "直连"
    try:
        p = urlparse(ensure_http_proxy_url(url))
        user = p.username or ""
        host = p.hostname or ""
        port = p.port or ""
        region = ""
        sid = ""
        m = re.search(r"region-([A-Za-z0-9]+)", user)
        if m:
            region = m.group(1)
        m = re.search(r"sid-([^-]+)", user)
        if m:
            sid = m.group(1)
        bits = [f"{p.scheme}://"]
        tag_parts = []
        if region:
            tag_parts.append(f"region-{region}")
        if sid:
            tag_parts.append(f"sid-{sid}")
        if tag_parts:
            bits.append("-".join(tag_parts) + "@")
        bits.append(f"{host}:{port}")
        return "".join(bits) if tag_parts else f"{p.scheme}://***@{host}:{port}"
    except Exception:
        return "已配置"


def set_region(proxy_url: str, region: str) -> str:
    region = (region or "").strip().upper()
    if not proxy_url or not region:
        return proxy_url
    url = ensure_http_proxy_url(proxy_url)
    if _REGION_RE.search(url):
        return _REGION_RE.sub(f"-region-{region}-", url, count=1)
    # user 段插入 region
    try:
        p = urlparse(url)
        user = p.username or ""
        if "region-" not in user and user:
            # account-sid-xxx → account-region-US-sid-xxx
            if "-sid-" in user:
                user = user.replace("-sid-", f"-region-{region}-sid-", 1)
            else:
                user = f"{user}-region-{region}"
            pwd = p.password or ""
            auth = f"{user}:{pwd}@" if pwd else f"{user}@"
            return f"{p.scheme}://{auth}{p.hostname}:{p.port or 2000}"
    except Exception:
        pass
    return url


def set_sid(proxy_url: str, sid: str | None = None, sid_len: int = 8) -> str:
    url = ensure_http_proxy_url(proxy_url)
    if not url:
        return url
    new_sid = sid or random_sid(sid_len)
    if _SID_RE.search(url):
        return _SID_RE.sub(f"-sid-{new_sid}-t-", url, count=1)
    # 无 sid 段时尝试在 user 末尾追加
    try:
        p = urlparse(url)
        user = p.username or ""
        if user and "-sid-" not in user:
            user = f"{user}-sid-{new_sid}-t-5"
            pwd = p.password or ""
            auth = f"{user}:{pwd}@" if pwd else f"{user}@"
            return f"{p.scheme}://{auth}{p.hostname}:{p.port or 2000}"
    except Exception:
        pass
    return url


def parse_proxy_auth(proxy_url: str) -> dict[str, Any]:
    url = ensure_http_proxy_url(proxy_url)
    p = urlparse(url)
    if not p.hostname:
        raise ValueError(f"无效代理 URL: {proxy_url}")
    user = p.username or ""
    password = p.password or ""
    return {
        "scheme": p.scheme or "http",
        "host": p.hostname,
        "port": int(p.port or 2000),
        "username": user,
        "password": password,
        "auth_header": base64.b64encode(f"{user}:{password}".encode()).decode() if user else "",
        "url": url,
    }


def build_dynamic_proxy(cfg: dict[str, Any], *, region: str | None = None, sid: str | None = None) -> str:
    """根据 config.proxy.dynamic 生成一条完整代理 URL（含随机 sid）。"""
    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    if not dyn.get("enabled"):
        return ""

    template = (dyn.get("template") or "").strip()
    if template:
        url = ensure_http_proxy_url(template)
    else:
        user = (dyn.get("user") or "").strip()
        password = (dyn.get("password") or "").strip()
        host = (dyn.get("host") or "us.lajiaohttp.net").strip()
        port = int(dyn.get("port") or 2000)
        reg = (region or dyn.get("region") or "US").strip().upper()
        sticky = int(dyn.get("sticky") or 5)
        # 支持 user 已含 region/sid，或纯账号
        if user and "region-" not in user and "sid-" not in user:
            user = f"{user}-region-{reg}-sid-PLACEHOLDER-t-{sticky}"
        elif user and "sid-" not in user:
            user = f"{user}-sid-PLACEHOLDER-t-{sticky}"
        url = f"http://{user}:{password}@{host}:{port}"

    reg = (region or dyn.get("region") or "").strip()
    if reg:
        url = set_region(url, reg)

    if dyn.get("rotate_sid", True) or sid or "{sid}" in (template or ""):
        # 占位符兼容
        if "{sid}" in url:
            url = url.replace("{sid}", sid or random_sid(int(dyn.get("sid_len") or 8)))
        if "{region}" in url:
            url = url.replace("{region}", (reg or dyn.get("region") or "US").upper())
        if "{sticky}" in url:
            url = url.replace("{sticky}", str(int(dyn.get("sticky") or 5)))
        url = set_sid(url, sid=sid, sid_len=int(dyn.get("sid_len") or 8))
    return url


def pick_proxy(cfg: dict[str, Any], override: str | None = None) -> str:
    """选择代理字符串（可能是动态 lajiao URL，尚未套链式本地口）。

    override:
      - None: 配置逻辑
      - "": 直连
      - 其他: 指定
    """
    if override is not None:
        return override

    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    if dyn.get("enabled"):
        return build_dynamic_proxy(cfg)

    pool = cfg.get("proxy", {}).get("pool") or []
    pool = [ensure_http_proxy_url(p) for p in pool if isinstance(p, str) and p.strip()]
    if pool:
        # 池内也支持 lajiao sid 随机
        chosen = random.choice(pool)
        if dyn.get("rotate_sid", True) and _SID_RE.search(chosen):
            return set_sid(chosen, sid_len=int(dyn.get("sid_len") or 8))
        return chosen
    return str(cfg.get("proxy", {}).get("default") or "")


def needs_chain(cfg: dict[str, Any], proxy_url: str) -> bool:
    if not proxy_url:
        return False
    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    chain_via = (dyn.get("chain_via") or "").strip()
    if not chain_via:
        return False
    # 本地地址不需要再套一层
    try:
        host = urlparse(ensure_http_proxy_url(proxy_url)).hostname or ""
        if host in {"127.0.0.1", "localhost"}:
            return False
    except Exception:
        pass
    # 默认：动态代理或含 lajiao 域名时走链路
    if dyn.get("enabled"):
        return True
    return "lajiaohttp" in proxy_url or bool(dyn.get("force_chain"))


@dataclass
class ResolvedProxy:
    """一次注册实际使用的代理。"""

    # 交给 curl_cffi 的 URL（可能是本地隧道口）
    session_url: str
    # 上游真实代理（辣椒等），用于日志
    upstream_url: str
    chain: "StickyChainTunnel | None" = None
    region: str = ""
    sid: str = ""
    # 探活时拿到的出口 ipinfo(country/timezone), Geo 对齐复用, 省单独 Geo 查询(+2s/号)
    ipinfo: dict = field(default_factory=dict)
    # 隧道探活是否通过(默认 True)。3 次探活均失败时置 False: 调用方应跳过 Geo 查询
    # (坏隧道查 ipwho.is 必失败, 半开黑洞可等 ~150s), 直接回退默认画像/靠注册链首请求
    # 失败换 sid 重试, 新隧道再正常 Geo。
    probe_ok: bool = True

    def label(self) -> str:
        base = proxy_label(self.upstream_url or self.session_url)
        if self.chain:
            return f"{base} via-chain"
        return base

    def close(self) -> None:
        if self.chain is not None:
            self.chain.close()
            self.chain = None


def _extract_region_sid(url: str) -> tuple[str, str]:
    user = urlparse(ensure_http_proxy_url(url)).username or ""
    region = ""
    sid = ""
    m = re.search(r"region-([A-Za-z0-9]+)", user)
    if m:
        region = m.group(1)
    m = re.search(r"sid-([^-]+)", user)
    if m:
        sid = m.group(1)
    return region, sid


def resolve_proxy(cfg: dict[str, Any], override: str | None = None) -> ResolvedProxy:
    """生成本次注册代理；需要时启动粘性链式隧道。

    隧道探活 + 失败重建(最多 3 次): 容忍 hop1(7890 Clash) 偶发抖动——
    实测 2/10 隧道 CONNECT 失败, 重建后 8/8 成功(失败是随机抖动, 重试大概率成功)。
    探活不阻塞: probe 超时计入, 重建只在探活失败时发生。
    """
    upstream = pick_proxy(cfg, override)
    if not upstream:
        return ResolvedProxy(session_url="", upstream_url="", region="", sid="")

    region, sid = _extract_region_sid(upstream)
    if needs_chain(cfg, upstream):
        dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
        hop1 = ensure_http_proxy_url(dyn.get("chain_via") or "http://127.0.0.1:10808")
        last_tunnel: StickyChainTunnel | None = None
        for attempt in range(3):
            if last_tunnel is not None:
                last_tunnel.close()
            tunnel = StickyChainTunnel(hop1=hop1, hop2=upstream)
            tunnel.start()
            last_tunnel = tunnel
            rp = ResolvedProxy(
                session_url=tunnel.local_url,
                upstream_url=upstream,
                chain=tunnel,
                region=region,
                sid=sid,
            )
            # 探活: 隧道能通到外网才算建好; 失败重建
            try:
                info = probe_proxy(tunnel.local_url, timeout=10)
                if info.get("status") == 200 and info.get("ip"):
                    if attempt > 0:
                        logger.warning("[Proxy] 隧道探活失败重建成功 (attempt %s)", attempt + 1)
                    rp.ipinfo = info.get("ipinfo") or {}  # Geo 对齐复用探活 ipinfo(省单独查询)
                    return rp
            except Exception:
                pass
            logger.warning("[Proxy] 隧道探活失败 (attempt %s), 重建中", attempt + 1)
        # 3 次都失败: 返回最后一个(注册链会按 outcome 失败重试), 标记探活失败——
        # 调用方据此跳过 Geo 查询(坏隧道查 ipwho.is 必失败, 黑洞白等 ~150s)
        logger.error("[Proxy] 隧道 3 次探活均失败, 返回最后隧道")
        rp.probe_ok = False
        return rp
    return ResolvedProxy(session_url=upstream, upstream_url=upstream, region=region, sid=sid)


def _read_until_headers(sock: socket.socket) -> tuple[bytes, bytes]:
    """读 HTTP 头，返回 (headers, extra)。

    extra = \r\n\r\n 之后已读到的多余字节——目标可能在 CONNECT 200 响应后立即发
    数据（SSH banner / IMAP greeting / TLS 前的协议首包），若被 recv(4096) 一起
    读走而丢弃，后续 relay 永远等不到这包数据 → 连接中止。必须保留并补发。
    """
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    idx = data.find(b"\r\n\r\n")
    if idx == -1:
        return data, b""
    return data[:idx], data[idx + 4 :]


def _parse_hop(url: str) -> tuple[str, int, str]:
    """返回 host, port, basic_auth_b64（可空）。"""
    info = parse_proxy_auth(url)
    return info["host"], info["port"], info["auth_header"]


def _socks5_recv_exact(sock: socket.socket, n: int) -> bytes:
    """从 sock 精确读 n 字节（socks5 协议固定长度帧）。"""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socks5 握手连接被断")
        buf += chunk
    return buf


def _socks5_connect(hop1_sock: socket.socket, host: str, port: int, username: str, password: str) -> None:
    """对已 CONNECT 到 hop2(socks5) 的连接做握手 + CONNECT 目标。失败抛异常。

    步骤: 方法协商 → 用户密码认证 → CONNECT host:port。
    """
    # 1) 方法协商: 支持 noauth + userpass
    hop1_sock.sendall(b"\x05\x02\x00\x02")
    method = _socks5_recv_exact(hop1_sock, 2)
    if method[0] != 0x05:
        raise ConnectionError(f"socks5 版本错误: {method[0]}")
    if method[1] == 0x02:
        # 用户密码认证
        u = username.encode() or b""
        p = password.encode() or b""
        if len(u) > 255 or len(p) > 255:
            raise ConnectionError("socks5 用户名/密码过长")
        hop1_sock.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        au = _socks5_recv_exact(hop1_sock, 2)
        if au[0] != 0x01 or au[1] != 0x00:
            raise ConnectionError(f"socks5 认证失败: {au.hex()}")
    elif method[1] != 0x00:
        raise ConnectionError(f"socks5 无可接受认证方法: {method[1]}")
    # 2) CONNECT host:port
    host_b = host.encode()
    if len(host_b) > 255:
        raise ConnectionError("socks5 目标主机名过长")
    hop1_sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + port.to_bytes(2, "big"))
    # 读 CONNECT 响应(可变长: ATYP 1 字节 + 地址)
    rep = _socks5_recv_exact(hop1_sock, 4)
    if rep[1] != 0x00:
        raise ConnectionError(f"socks5 CONNECT 失败 code={rep[1]}")
    atyp = rep[3]
    if atyp == 0x01:  # IPv4
        _socks5_recv_exact(hop1_sock, 4 + 2)
    elif atyp == 0x04:  # IPv6
        _socks5_recv_exact(hop1_sock, 16 + 2)
    elif atyp == 0x03:  # 域名
        ln = _socks5_recv_exact(hop1_sock, 1)[0]
        _socks5_recv_exact(hop1_sock, ln + 2)
    else:
        raise ConnectionError(f"socks5 未知 ATYP: {atyp}")


class StickyChainTunnel:
    """本地 HTTP 代理：client → hop1(10808) → CONNECT/socks5 hop2 → 目标。

    同一 tunnel 实例固定 hop2（含 sid），保证单次注册出口 IP 粘性。
    hop2 为 socks5 时走 _socks5_connect 握手(1024proxy 是 socks5 服务)。
    """

    def __init__(self, hop1: str, hop2: str, bind_host: str = "127.0.0.1"):
        self.hop1_url = ensure_http_proxy_url(hop1)
        self.hop2_url = ensure_http_proxy_url(hop2)
        self.bind_host = bind_host
        # hop2 协议: socks5 走握手, http/https 走 CONNECT(1024proxy 是 socks5 服务,
        # 若用 HTTP CONNECT 转发会目标连不上——实测 socks5 握手+CONNECT+数据全通)
        self.hop2_scheme = "socks5" if self.hop2_url.startswith("socks") else "http"
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port = 0
        self.local_url = ""

    def start(self) -> str:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind_host, 0))
        srv.listen(64)
        srv.settimeout(1.0)
        self._sock = srv
        self.port = srv.getsockname()[1]
        self.local_url = f"socks5h://{self.bind_host}:{self.port}"
        self._thread = threading.Thread(
            target=self._serve,
            name=f"chain-{self.port}",
            daemon=True,
        )
        self._thread.start()
        logger.debug(
            "[Proxy] 链式隧道 %s → %s → %s",
            self.local_url,
            proxy_label(self.hop1_url),
            proxy_label(self.hop2_url),
        )
        return self.local_url

    def close(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(client,), daemon=True)
            t.start()

    def _handle(self, client: socket.socket) -> None:
        hop1_sock: socket.socket | None = None
        try:
            client.settimeout(120)
            hop1_host, hop1_port, hop1_auth = _parse_hop(self.hop1_url)
            hop2_host, hop2_port, hop2_auth = _parse_hop(self.hop2_url)
            _pa = "Proxy-" + "Authorization"
            _basic = "Bas" + "ic "

            # 1) SOCKS5 方法协商: client -> [ver=5, nmethods, methods...]
            hdr = _socks5_recv_exact(client, 2)
            if hdr[0] != 0x05:
                return
            _socks5_recv_exact(client, hdr[1])
            client.sendall(b"\x05\x00")

            # 2) SOCKS5 CONNECT 请求: [ver=5, cmd=1, rsv=0, atyp, addr, port]
            req = _socks5_recv_exact(client, 4)
            if req[0] != 0x05 or req[1] != 0x01:
                client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            atyp = req[3]
            if atyp == 0x01:
                host = socket.inet_ntoa(_socks5_recv_exact(client, 4))
            elif atyp == 0x03:
                ln = _socks5_recv_exact(client, 1)[0]
                host = _socks5_recv_exact(client, ln).decode(errors="replace")
            elif atyp == 0x04:
                host = socket.inet_ntop(socket.AF_INET6, _socks5_recv_exact(client, 16))
            else:
                client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            port = int.from_bytes(_socks5_recv_exact(client, 2), "big")

            hop1_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            hop1_sock.settimeout(120)
            hop1_sock.connect((hop1_host, hop1_port))

            # 3) 经 hop1 CONNECT 到 hop2 代理主机
            connect_hop2 = (
                "CONNECT " + hop2_host + ":" + str(hop2_port) + " HTTP/1.1\r\n"
                + "Host: " + hop2_host + ":" + str(hop2_port) + "\r\n"
            )
            if hop1_auth:
                connect_hop2 += _pa + ": " + _basic + hop1_auth + "\r\n"
            connect_hop2 += "\r\n"
            hop1_sock.sendall(connect_hop2.encode())
            hop1_resp, _ = _read_until_headers(hop1_sock)
            hop1_status = hop1_resp.split(b"\r\n", 1)[0]
            if b" 200 " not in hop1_status:
                client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
                return

            # 4) 对 hop2 握手 + CONNECT 真实目标
            if self.hop2_scheme == "socks5":
                _hop2_info = parse_proxy_auth(self.hop2_url)
                _socks5_connect(
                    hop1_sock, host=host, port=port,
                    username=_hop2_info.get("username") or "",
                    password=_hop2_info.get("password") or "",
                )
            else:
                connect_target = "CONNECT " + host + ":" + str(port) + " HTTP/1.1\r\n" + "Host: " + host + ":" + str(port) + "\r\n"
                if hop2_auth:
                    connect_target += _pa + ": " + _basic + hop2_auth + "\r\n"
                connect_target += "\r\n"
                hop1_sock.sendall(connect_target.encode())
                hop2_resp, _ = _read_until_headers(hop1_sock)
                hop2_status = hop2_resp.split(b"\r\n", 1)[0]
                if b" 200 " not in hop2_status:
                    client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
                    return

            # 5) SOCKS5 CONNECT 响应(成功)
            client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")

            # 6) 双向透传
            def relay(src: socket.socket, dst: socket.socket) -> None:
                try:
                    while True:
                        try:
                            data = src.recv(65536)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        dst.sendall(data)
                except OSError:
                    pass
                finally:
                    try:
                        dst.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass

            t1 = threading.Thread(target=relay, args=(client, hop1_sock), daemon=True)
            t2 = threading.Thread(target=relay, args=(hop1_sock, client), daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=300)
            t2.join(timeout=300)
        except Exception as exc:
            logger.debug("[Proxy] 隧道连接异常: %s", exc)
        finally:
            for s in (client, hop1_sock):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass



# 国家 → (语言, languages, 标准时区) —— Geo 对齐(register-kit GeoProfile 借鉴)
_GEO_LOCALE: dict[str, tuple[str, str, str]] = {
    "US": ("en-US", "en-US,en;q=0.9", "America/Los_Angeles"),
    "JP": ("ja-JP", "ja-JP,ja;q=0.9,en-US;q=0.8", "Asia/Tokyo"),
    "SG": ("en-SG", "en-SG,en;q=0.9", "Asia/Singapore"),
    "HK": ("zh-HK", "zh-HK,zh;q=0.9,en-US;q=0.8", "Asia/Hong_Kong"),
    "TW": ("zh-TW", "zh-TW,zh;q=0.9,en-US;q=0.8", "Asia/Taipei"),
    "KR": ("ko-KR", "ko-KR,ko;q=0.9,en-US;q=0.8", "Asia/Seoul"),
    "GB": ("en-GB", "en-GB,en;q=0.9", "Europe/London"),
    "DE": ("de-DE", "de-DE,de;q=0.9,en-US;q=0.8", "Europe/Berlin"),
    "FR": ("fr-FR", "fr-FR,fr;q=0.9,en-US;q=0.8", "Europe/Paris"),
    "NL": ("nl-NL", "nl-NL,nl;q=0.9,en-US;q=0.8", "Europe/Amsterdam"),
    "CA": ("en-CA", "en-CA,en;q=0.9,fr-CA;q=0.8", "America/Toronto"),
}


def geo_profile_for_proxy(proxy_url: str, ipinfo: dict | None = None, timeout: int = 15) -> dict[str, str]:
    """查代理出口 IP 的地理画像(国家/语言/时区), 失败回退 US。

    register-kit GeoProfile 借鉴: 语言/时区随出口 IP, 避免"时区/语言与出口地理位置
    对不上"被风控当设备指纹矛盾。调用方在注册时查一次, 贯穿整条注册链。
    ipinfo: 隧道探活已拿到的 ipinfo(country/timezone), 有则**复用不查 ipwho.is**(省 ~2s/号)。
    返回 {country, language, languages, timezone, ip}。
    """
    out = {"country": "US", "language": "en-US", "languages": "en-US,en;q=0.9",
           "timezone": "America/Los_Angeles", "ip": ""}

    # 优先复用探活 ipinfo(隧道探活 probe_proxy 已拿 ipinfo.io country/timezone)
    if isinstance(ipinfo, dict) and ipinfo.get("country"):
        cc = str(ipinfo["country"]).upper()
        out["country"] = cc
        out["ip"] = str(ipinfo.get("ip") or "")
        lang, langs, _ = _GEO_LOCALE.get(cc, ("en-US", "en-US,en;q=0.9", "America/Los_Angeles"))
        out["language"], out["languages"] = lang, langs
        _tz = str(ipinfo.get("timezone") or "")
        out["timezone"] = _tz or _GEO_LOCALE.get(cc, ("en-US", "", "America/Los_Angeles"))[2]
        logger.info("[Geo] 复用探活 %s → %s  lang=%s tz=%s", out["ip"], cc, lang, out["timezone"])
        return out

    # 无 ipinfo: 查 ipwho.is 兜底(隧道探活没拿到 ipinfo 时)
    from curl_cffi.requests import Session

    s = Session(impersonate="chrome142", verify=False)
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}
    try:
        r = s.get("https://ipwho.is/", timeout=timeout)
        d = r.json() if r.status_code == 200 else {}
        cc = str(d.get("country_code") or "US").upper()
        out["country"] = cc
        out["ip"] = str(d.get("ip") or "")
        # 语言按国家映射(未知国家回退 en-US); 时区优先出口实际值(ipwho.is)
        lang, langs, _ = _GEO_LOCALE.get(cc, ("en-US", "en-US,en;q=0.9", "America/Los_Angeles"))
        out["language"], out["languages"] = lang, langs
        _tz_raw = d.get("timezone")
        if isinstance(_tz_raw, dict):
            _tz_actual = str(_tz_raw.get("id") or "")
        elif isinstance(_tz_raw, str):
            _tz_actual = _tz_raw
        else:
            _tz_actual = ""
        out["timezone"] = _tz_actual or _GEO_LOCALE.get(cc, ("en-US", "", "America/Los_Angeles"))[2]
        logger.info("[Geo] 出口 %s → %s  lang=%s tz=%s", out["ip"], cc, lang, out["timezone"])
    except Exception as exc:
        logger.warning("[Geo] 查询失败(%s), 回退 US", str(exc)[:60])
    return out


# 移动运营商 NAT 出口(register-kit is_mobile_carrier_ip 对齐): 同一公网 IP 被海量
# 真实用户+滥用流量共享, 账号几十分钟内被回收; 固定宽带住宅存活率明显更高。
MOBILE_CARRIER_HINTS = (
    "verizon", "t-mobile", "tmobile", "myvzw", "sprint", "cricket",
    "metro", "boost", "cellco", "at&t mobility", "att wireless",
)


def _is_mobile_carrier_ip(ipinfo: dict) -> bool:
    """出口 IP 的 org/hostname 命中移动运营商 NAT(低存活率) → True。"""
    text = ((ipinfo.get("org") or "") + " " + (ipinfo.get("hostname") or "")).lower()
    return any(h in text for h in MOBILE_CARRIER_HINTS)


def probe_proxy(session_url: str, timeout: int = 20) -> dict[str, Any]:
    """探测出口 IP。"""
    from curl_cffi.requests import Session

    if not session_url:
        proxies = None
    else:
        proxies = {"http": session_url, "https": session_url}
    s = Session(impersonate="chrome142", verify=False)
    if proxies:
        s.proxies = proxies
    r = s.get("https://api.ipify.org?format=json", timeout=timeout)
    ip = ""
    try:
        ip = (r.json() or {}).get("ip") or ""
    except Exception:
        ip = (r.text or "")[:80]
    info: dict[str, Any] = {"status": r.status_code, "ip": ip, "body": (r.text or "")[:200]}
    try:
        r2 = s.get("https://ipinfo.io/json", timeout=timeout)
        if r2.status_code == 200:
            info["ipinfo"] = r2.json()
    except Exception:
        pass
    return info


class ProxyPool:
    """动态代理隧道池：预建 N 条不同 sid 的粘性隧道，并发账号各取一条。

    实验(2026-08-12): 8 条并行建 6.6s, 出口 IP 8/8 分散(不同 C 段), sid 粘性 12s+。
    相比每次 resolve_proxy 现场建隧道(2-5s/次), 池只建一次复用:
      - 免每次建隧道+探活开销(8 并发省 ~20-30s)
      - 并发出口数固定可控(避免瞬时 N 个隧道全建)
      - 账号级出口粘性(sid t-N 分钟)

    用法:
        pool = ProxyPool(cfg, size=8)
        rp = pool.acquire()      # ResolvedProxy(含隧道)
        ... 注册 ...
        pool.release(rp)         # 归还(隧道复用)
        pool.close()
    """

    def __init__(self, cfg: dict[str, Any], size: int = 4, region: str | None = None):
        self.cfg = cfg
        self.region = region
        # FIFO 空闲队列: acquire 从队头拿(popleft), release 追加队尾 —— 轮换使用所有隧道
        # (LIFO pop 末尾 + append 末尾会永远拿刚归还的同一条, 实测 IP 1/5)
        self._idle: deque[ResolvedProxy] = deque()
        self._all: list[ResolvedProxy] = []
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._closed = False
        self._build(size)

    # ---- 建隧道 ----
    def _build_one(self) -> ResolvedProxy:
        # 并发建隧道时 cliproxy/动态代理偶发 TLS/探活失败(实测 2 条并发 1 条 TLS 错)。
        # 探活失败/地区不符换新 sid 重试(最多 5 次), 否则并发 worker 拿不到隧道就卡住。
        # 2026-08-14: 代理源质量下降(US region 混入 VE 出口), 加地区校验 + 重试 5 次 + 间隔 1s。
        last = ""
        want_cc = (self.region or "").upper()
        last_mobile: ResolvedProxy | None = None  # 最后一条移动运营商(5次筛不到时"将就"用)
        for attempt in range(1, 6):
            upstream = build_dynamic_proxy(self.cfg, region=self.region)
            if not upstream:
                raise RuntimeError("动态代理未配置(proxy.dynamic.enabled)")
            dyn = (self.cfg.get("proxy") or {}).get("dynamic") or {}
            hop1 = ensure_http_proxy_url(dyn.get("chain_via") or "http://127.0.0.1:10808")
            tunnel = StickyChainTunnel(hop1=hop1, hop2=upstream)
            tunnel.start()
            try:
                info = probe_proxy(tunnel.local_url, timeout=12)
                if info.get("status") == 200 and info.get("ip"):
                    # 地区校验: 非目标 region 的出口 IP 丢弃(如 US region 混入 VE 出口, 连接不稳 + 风控)
                    ipinfo = info.get("ipinfo") or {}
                    cc = str(ipinfo.get("country") or "").upper()
                    if want_cc and cc and cc != want_cc:
                        last = f"出口地区不符 want={want_cc} got={cc} ip={info.get('ip')}"
                        tunnel.close()
                        time.sleep(1.0)
                        continue
                    region, sid = _extract_region_sid(upstream)
                    rp = ResolvedProxy(
                        session_url=tunnel.local_url, upstream_url=upstream,
                        chain=tunnel, region=region, sid=sid,
                        ipinfo=info.get("ipinfo") or {},  # Geo 对齐复用探活 ipinfo
                    )
                    # 移动运营商筛选(register-kit is_mobile_carrier_ip 对齐): 移动 NAT 出口存活率低, 换 sid 重抽;
                    # 5 次都筛不到固定宽带时, 用最后一条移动运营商"将就"(register-kit find_residential_sid 同款兜底)
                    if _is_mobile_carrier_ip(ipinfo):
                        if last_mobile is not None:
                            last_mobile.close()
                        last_mobile = rp
                        last = f"移动运营商出口 org={ipinfo.get('org')} ip={info.get('ip')}"
                        time.sleep(1.0)
                        continue
                    if last_mobile is not None:
                        last_mobile.close()
                    return rp
                last = f"探活 status={info.get('status')} ip={info.get('ip')}"
            except Exception as exc:
                last = str(exc)[:80]
            tunnel.close()
            if attempt < 5:
                time.sleep(1.0)
        if last_mobile is not None:
            logger.warning("[Proxy] 5 次都没筛到固定宽带, 用最后一条移动运营商(%s)将就",
                           last_mobile.ipinfo.get("org") or "?")
            return last_mobile
        raise RuntimeError(f"隧道建失败(5次换sid重试): {last}")

    def _build(self, size: int) -> None:
        from concurrent.futures import ThreadPoolExecutor

        built = 0
        with ThreadPoolExecutor(max_workers=max(1, size)) as ex:
            futs = [ex.submit(self._build_one) for _ in range(size)]
            for f in futs:
                try:
                    rp = f.result()
                    self._idle.append(rp)
                    self._all.append(rp)
                    built += 1
                except Exception as exc:
                    logger.warning("[ProxyPool] 建隧道失败: %s", str(exc)[:100])
        if built < max(1, size):
            logger.warning("[ProxyPool] 建池不完整: %s/%s", built, size)
        logger.info("[ProxyPool] 预建 %s 条隧道(size=%s)", built, size)

    # ---- 领取/归还 ----
    def acquire(self, timeout: float = 30.0) -> ResolvedProxy:
        with self._cond:
            if self._closed:
                raise RuntimeError("ProxyPool 已关闭")
            if not self._idle:
                self._cond.wait(timeout)
            if self._idle:
                return self._idle.popleft()
            # 仍无空闲: 现场补建一条(池被打穿时)
            rp = self._build_one()
            self._all.append(rp)
            return rp

    def release(self, rp: ResolvedProxy | None) -> None:
        if rp is None:
            return
        with self._cond:
            if self._closed:
                rp.close()
                return
            self._idle.append(rp)
            self._cond.notify()

    def discard(self, rp: ResolvedProxy | None) -> None:
        """丢弃一条(注册失败, 隧道可能已坏)——关闭 + 补建保持池大小。

        代理质量问题自动自愈: 坏隧道不归还污染池, 立即换新 sid 补一条。
        """
        if rp is None:
            return
        rp.close()
        with self._cond:
            if self._closed:
                return
            try:
                # 先移除被丢弃的旧隧道, 再补建一条 → 池大小保持
                if rp in self._all:
                    self._all.remove(rp)
                new_rp = self._build_one()
                self._all.append(new_rp)
                self._idle.append(new_rp)
                self._cond.notify()
                logger.info("[ProxyPool] 坏隧道已丢弃并补建, size=%s idle=%s", self.size(), self.idle())
            except Exception as exc:
                logger.warning("[ProxyPool] 补建失败: %s", str(exc)[:100])

    def size(self) -> int:
        return len(self._all)

    def idle(self) -> int:
        return len(self._idle)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        for rp in self._all:
            rp.close()
        self._all = []
        self._idle.clear()

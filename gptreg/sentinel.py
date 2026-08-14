"""Sentinel token 生成（纯 Python PoW — 注册默认路径）。

零外部依赖：直连 sentinel.openai.com，FNV-1a PoW，t=""（不传 turnstile）。
通常无 openai-sentinel-so-token。真 so 见 browser_sentinel（protocol.sentinel_source=browser）。

对照「神奇的小PP」protocol_register：
- 已学：Datadog、sv、~S、answer SDK、create 侧 so-token 结构
- pow_so_source=xiaopp：create 带小PP 同款 HAR so（纯协议，无浏览器）
- 仍过滤 SyntaxError/jsdom 假 so；browser 真 so 仍走 browser_sentinel

诊断：summarize_chatreq / log_chatreq_obs 只观测 /req 的 so.required 等，不产 so。
Node runner / sentinel_proxy 保留为兼容代码，注册主路径默认不走。

P1（2026-07）：本环境 create 有/无 so ≥2h 双活 → 默认保持 pow。
证据：capture/p1-so-survival-20260712/FINDINGS.md
"""
from __future__ import annotations

import base64
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FLOW_PAGE_URL = {
    "authorize_continue": "https://auth.openai.com/email-verification",
    "oauth_create_account": "https://auth.openai.com/about-you",
    "username_password_create": "https://auth.openai.com/create-account/password",
}

_DEFAULT_SENTINEL_SV = "20260219f9f6"  # 对齐 config.yaml / 小PP 2026-02
_MAX_POW_ATTEMPTS = 500_000


# chatReq 观测(诊断)已拆分到 sentinel_chatreq.py, 此处 re-export 保持引用兼容
from gptreg.sentinel_chatreq import log_chatreq_obs, summarize_chatreq  # noqa: E402,F401

# ═══════════════════════════════════════════════════════════════
# 纯 Python PoW Sentinel（当前主力）
# ═══════════════════════════════════════════════════════════════


class SentinelPoW:
    """纯 Python FNV-1a PoW sentinel 生成器。

    用法:
        pow = SentinelPoW(ua=session.user_agent)
        token_json = pow.build(session.raw_session, device_id, flow)
        # token_json = '{"p":"gAAAAAB...", "t":"", "c":"...", "id":"...", "flow":"..."}'
    """

    def __init__(
        self,
        *,
        ua: str = "",
        sv: str = "",
        device_id: str = "",
        cores: int | None = None,
        screen_w: int = 1920,
        screen_h: int = 1080,
    ):
        self.ua = ua or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        )
        self.sv = sv or _DEFAULT_SENTINEL_SV
        # 指纹内嵌 id 对齐 token.id / oai-did（chatgpt_register 参考：gather_fingerprint_data(sid)）。
        # 不传则回退独立 UUID（starmiaoa 旧行为），保证旧调用点不回归。
        self._sid = device_id or str(__import__("uuid").uuid4())
        # 读配置 hardwareConcurrency；未提供则保留随机多样性
        self.cores = int(cores) if cores else None
        # screen.width + screen.height（int，对齐 SDK / 参考实现，非 "1920x1080" 字符串）
        self.screen = int(screen_w) + int(screen_h)

    # ── FNV-1a 32-bit ─────────────────────────────────────────

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        return format(h & 0xFFFFFFFF, "08x")

    # ── 指纹配置 ──────────────────────────────────────────────

    def _config(self, *, answer: bool = False) -> list:
        """指纹数组。

        布局仍兼容 starmiaoa/k12（本环境 Eric/Embree create 200 用过）。
        从「神奇的小PP」吸收且不碰假 so 的点：
        - answer 阶段 SDK 用 backend-api 路径（小PP `_ANS_SDK_URL`）
        - req 阶段用 `sentinel/{sv}/sdk.js`
        FNV 算法保持 k12 字符哈希（已验证可解 PoW）；不搬 `_HAR_SO`。
        """
        perf_now = random.uniform(1000, 50000)
        sdk = (
            "https://sentinel.openai.com/backend-api/sentinel/sdk.js"
            if answer
            else f"https://sentinel.openai.com/sentinel/{self.sv}/sdk.js"
        )
        return [
            self.screen,
            time.strftime(
                "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)",
                time.gmtime(),
            ),
            4294705152,
            random.random(),
            self.ua,
            sdk,
            None,
            None,
            "en-US",
            random.random(),
            random.choice(
                [
                    "vendorSub-undefined",
                    "plugins-undefined",
                    "mimeTypes-undefined",
                    "hardwareConcurrency-undefined",
                ]
            ),
            random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
            random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
            perf_now,
            self._sid,
            "",
            self.cores if self.cores else random.choice([4, 8, 12, 16]),
            time.time() * 1000 - perf_now,
        ]

    @staticmethod
    def _b64(data) -> str:
        return base64.b64encode(
            json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).decode("ascii")

    # ── PoW 求解 ──────────────────────────────────────────────

    def _requirements_token(self) -> str:
        data = self._config(answer=False)
        data[3] = 1
        data[9] = round(random.uniform(5, 50))
        # 小PP / sentinel.go：requirements 也带 ~S
        return "gAAAAAC" + self._b64(data) + "~S"

    def _solve_pow(self, seed: str, difficulty: str) -> str:
        start = time.time()
        data = self._config(answer=True)
        difficulty = str(difficulty or "0")
        diff_len = len(difficulty)
        for i in range(_MAX_POW_ATTEMPTS):
            data[3] = i
            data[9] = round((time.time() - start) * 1000)
            payload = self._b64(data)
            if self._fnv1a_32(seed + payload)[:diff_len] <= difficulty:
                return "gAAAAAB" + payload + "~S"
        return "gAAAAAB" + self._b64("e") + "~S"

    # ── 主入口 ────────────────────────────────────────────────

    def build(self, raw_session, device_id: str, flow: str) -> str:
        """完整 PoW 流程：POST sentinel/req → 解 PoW → 返回 token JSON 字符串。"""
        requirements = self._requirements_token()
        resp = raw_session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            data=json.dumps({"p": requirements, "id": device_id, "flow": flow}),
            headers={
                "Content-Type": "text/plain;charset=UTF-8",
                "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
                "Origin": "https://sentinel.openai.com",
                "User-Agent": self.ua,
            },
            timeout=20,
        )
        try:
            data = resp.json() if resp.text else {}
        except Exception:
            data = {}
        # 诊断：服务端是否要求 so / 是否下发 collector_dx（不产 so）
        self.last_chatreq_obs = log_chatreq_obs(data, flow=flow, http=getattr(resp, "status_code", None))
        self.last_chatreq = data if isinstance(data, dict) else {}
        token = str(data.get("token") or "").strip()
        if resp.status_code != 200 or not token:
            raise RuntimeError(
                f"sentinel/req failed http={resp.status_code} flow={flow} "
                f"body={(resp.text or '')[:200]}"
            )

        pow_data = data.get("proofofwork") or {}
        p_value = (
            self._solve_pow(
                str(pow_data.get("seed") or ""),
                str(pow_data.get("difficulty") or "0"),
            )
            if pow_data.get("required") and pow_data.get("seed")
            else requirements
        )

        result = {
            "p": p_value,
            "t": "",
            "c": token,
            "id": device_id,
            "flow": flow,
        }
        return json.dumps(result, separators=(",", ":"))


# ═══════════════════════════════════════════════════════════════
# 兼容层（旧 Node runner / 手动 PoW — 保留但不被注册主路径使用）
# ═══════════════════════════════════════════════════════════════


def _imul(a: int, b: int) -> int:
    return (a * b) & 0xFFFFFFFF


def fnv1a_hash(text: str) -> str:
    return SentinelPoW._fnv1a_32(text)


def generate_fingerprint_config(
    cfg: dict[str, Any], device_id: str, attempt: int = 1, elapsed_ms: float = 0
) -> list:
    browser = cfg.get("browser", {})
    protocol = cfg.get("protocol", {})
    ua = browser.get("user_agent", "")
    language = browser.get("language", "ja-JP")
    languages = browser.get("languages", "ja-JP,ja,en-US,en")
    width = int(browser.get("screen_width", 1920))
    height = int(browser.get("screen_height", 1080))
    cores = int(browser.get("hardware_concurrency", 16))
    sv = protocol.get("sentinel_sv", "20260219f9f6")
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)")
    time_origin = time.time() * 1000 - random.uniform(1000, 5000)
    perf_now = random.uniform(1000, 50000)
    nav_props = [
        "clipboard−[object Clipboard]",
        "getBattery−function getBattery() { [native code] }",
        "sendBeacon−function sendBeacon() { [native code] }",
        "vibrate−function vibrate() { [native code] }",
    ]
    return [
        width + height,
        date_str,
        4294705152,
        attempt,
        ua,
        f"https://sentinel.openai.com/sentinel/{sv}/sdk.js",
        None,
        language,
        languages,
        round(elapsed_ms) if elapsed_ms else random.randint(1, 100),
        random.choice(nav_props),
        "_reactListening"
        + "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=11)),
        random.choice(
            ["requestIdleCallback", "webkitRequestAnimationFrame", "onfocus", "onblur"]
        ),
        round(perf_now, 10),
        str(device_id),
        "",
        cores,
        round(time_origin, 1),
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]


def encode_config(config: list) -> str:
    json_str = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(json_str.encode("utf-8")).decode("ascii")


def generate_requirements_token(cfg: dict[str, Any], device_id: str) -> str:
    config = generate_fingerprint_config(cfg, device_id, attempt=1, elapsed_ms=0)
    return "gAAAAAC" + encode_config(config) + "~S"


def build_sentinel_request_body(p: str, device_id: str, flow: str) -> str:
    return json.dumps({"p": p, "id": device_id, "flow": flow}, separators=(",", ":"))


def _resolve_node() -> str:
    override = os.environ.get("NODE_EXECUTABLE")
    if override:
        return override
    return "node.exe" if sys.platform.startswith("win") else "node"


def _sentinel_paths(cfg: dict[str, Any]) -> tuple[Path, Path]:
    root = Path(cfg.get("_root") or Path(__file__).resolve().parent.parent)
    sdir = Path(cfg.get("protocol", {}).get("sentinel_dir", "vendor/sentinel"))
    if not sdir.is_absolute():
        sdir = root / sdir
    return sdir / "sentinel-runner.js", sdir / "sdk.js"


def _browser_runner_args(
    cfg: dict[str, Any],
    *,
    flow: str,
    device_id: str,
    user_agent: str | None,
    page_url: str | None,
) -> tuple[list[str], Path, Path, str]:
    runner, sdk = _sentinel_paths(cfg)
    if not runner.exists():
        raise FileNotFoundError(f"找不到 sentinel-runner.js: {runner}")
    if not sdk.exists():
        raise FileNotFoundError(f"找不到 sdk.js: {sdk}")
    browser = cfg.get("browser", {})
    protocol = cfg.get("protocol", {})
    ua = user_agent or browser.get("user_agent", "")
    page = page_url or FLOW_PAGE_URL.get(flow, "https://auth.openai.com/email-verification")
    width = str(int(browser.get("screen_width", 1920)))
    height = str(int(browser.get("screen_height", 1080)))
    cores = str(int(browser.get("hardware_concurrency", 16)))
    language = browser.get("language", "en-US")
    languages = browser.get("languages", "en-US,en")
    sv = str(protocol.get("sentinel_sv") or _DEFAULT_SENTINEL_SV)
    script_src = str(
        protocol.get("sentinel_script_src")
        or f"https://sentinel.openai.com/sentinel/{sv}/sdk.js"
    )
    cmd = [
        _resolve_node(),
        str(runner),
        "--flow", flow,
        "--device-id", device_id,
        "--page-url", page,
        "--user-agent", ua,
        "--sdk", str(sdk),
        "--script-src", script_src,
        "--width", width,
        "--height", height,
        "--cores", cores,
        "--language", language,
        "--languages", languages,
        "--no-cookie",
    ]
    return cmd, runner, sdk, page


def _run_sentinel_node(cfg: dict[str, Any], cmd: list[str], *, flow: str, mode: str) -> str:
    env = os.environ.copy()
    env["SENTINEL_CONFIG"] = "__none__"
    logger.info("[Sentinel] Node runner 生成 token, flow=%s mode=%s", flow, mode)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            timeout=60, env=env, cwd=str(Path(cfg.get("_root") or ".")),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"sentinel-runner 超时 flow={flow} mode={mode}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 Node，请安装 Node.js 或设置 NODE_EXECUTABLE") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"sentinel-runner 退出码 {proc.returncode} mode={mode}\n"
            f"stderr: {(proc.stderr or '').strip()}\n"
            f"stdout: {(proc.stdout or '').strip()}"
        )
    token_text = (proc.stdout or "").strip()
    if not token_text:
        raise RuntimeError(
            f"sentinel-runner 输出为空 mode={mode}: {(proc.stderr or '').strip()}"
        )
    parsed = json.loads(token_text)
    for key in ("p", "c", "id", "flow"):
        if key not in parsed:
            raise RuntimeError(f"runner 输出缺少字段 {key}: {token_text[:180]}")
    logger.info(
        "[Sentinel] token 就绪 flow=%s mode=%s keys=%s has_t=%s has_so=%s",
        flow, mode, list(parsed.keys()),
        bool(parsed.get("t")), bool(parsed.get("so")),
    )
    return token_text


def generate_sentinel_token_via_node(
    cfg: dict[str, Any],
    challenge: dict | None,
    flow: str,
    device_id: str,
    user_agent: str | None = None,
    page_url: str | None = None,
    *,
    challenge_url: str | None = None,
) -> str:
    """Node runner 路径（兼容保留）。"""
    cmd, _runner, _sdk, _page = _browser_runner_args(
        cfg, flow=flow, device_id=device_id, user_agent=user_agent, page_url=page_url,
    )
    if challenge_url:
        cmd = cmd[:3] + ["--challenge-url", challenge_url] + cmd[3:]
        return _run_sentinel_node(cfg, cmd, flow=flow, mode="url")
    if not challenge:
        raise ValueError("challenge-file 模式需要 challenge dict")
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix=f"sentinel-{flow}-",
        delete=False, encoding="utf-8",
    )
    try:
        json.dump(challenge, tmp, ensure_ascii=False)
        tmp.flush(); tmp.close()
        cmd = cmd[:3] + ["--challenge-file", tmp.name] + cmd[3:]
        return _run_sentinel_node(cfg, cmd, flow=flow, mode="file")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

# so 头构造已拆分到 sentinel_so.py, 此处 re-export 保持引用兼容
from gptreg.sentinel_so import (  # noqa: E402,F401
    build_so_header, build_xiaopp_so_header, require_so_if_needed,
    resolve_pow_so_header, token_has_so,
)

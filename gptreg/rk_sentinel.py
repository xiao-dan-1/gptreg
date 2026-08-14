# -*- coding: utf-8 -*-
"""sentinel 真 SDK token 生成(打包自 chatgpt-free-agentid / oai_auth_core)。

链路: 本模块 → sentinel/sentinel-runner.js(vm 沙箱跑真 sdk.js,不需要 jsdom)
     → runner 自己发 sentinel/req(经本地中转 sentinel_proxy.py:1789,curl_cffi 过 CF)
     → 真 SDK 解 dx + PoW + 组装 → 完整 token 字符串。

★ 绝不回退到纯 Python 伪造 t/so 版:纯 Python PoW 能过 /sentinel/req 表层(返 200),
但服务端派 OTP 前跑真 sdk.js 深层校验会判非浏览器 → OTP 静默不下发。失败一律 raise。

前置: ① node 在 PATH; ② 本地中转 :1789 已起(ensure_sentinel_proxy() 负责)。
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "..", "vendor", "sentinel")
_RUNNER = os.path.join(_VENDOR, "rk_sentinel_runner.js")
_SDK = os.path.join(_VENDOR, "rk_sdk.js")
_PROXY_SERVER = os.path.join(_HERE, "rk_sentinel_proxy.py")
_SV = "20260219f9f6"
_CHALLENGE_URL = "http://127.0.0.1:1789/req"

_FLOW_PAGE = {
    "username_password_create": "https://auth.openai.com/create-account/password",
    "authorize_continue": "https://auth.openai.com/create-account",
    "oauth_create_account": "https://auth.openai.com/about-you",
    "password_verify": "https://auth.openai.com/log-in/password",
    "email_otp_validate": "https://auth.openai.com/email-verification",
    "password_reset": "https://auth.openai.com/reset-password/new-password",
}


class SentinelError(RuntimeError):
    """sentinel token 生成失败(node 不可用 / :1789 不通 / runner 非零退出 / 超时 / 空输出)。"""


def _run_runner(device_id, flow, user_agent, page_url, language, languages,
                width, height, cores, timezone, timeout, with_so=False):
    if not (device_id and flow and user_agent):
        raise SentinelError("gen_sentinel_token 缺参: device_id/flow/user_agent 必填")
    page = page_url or _FLOW_PAGE.get(flow, _FLOW_PAGE["username_password_create"])
    node_exe = "node.exe" if os.name == "nt" else "node"
    cmd = [node_exe, _RUNNER, "--challenge-url", _CHALLENGE_URL, "--flow", flow,
           "--device-id", device_id, "--page-url", page, "--user-agent", user_agent,
           "--sdk", _SDK, "--script-src", "https://sentinel.openai.com/sentinel/" + _SV + "/sdk.js",
           "--no-build-id", "--width", str(width), "--height", str(height), "--cores", str(cores),
           "--language", language, "--languages", languages, "--timezone", timezone, "--no-cookie"]
    if with_so:
        cmd.append("--with-so")
    env = os.environ.copy()
    env["SENTINEL_CONFIG"] = "__none__"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              timeout=timeout, env=env, cwd=os.path.dirname(_RUNNER))
    except FileNotFoundError as e:
        raise SentinelError("node 不在 PATH 或 runner 缺失: %s" % e)
    except subprocess.TimeoutExpired:
        raise SentinelError("sentinel runner 超时(%ds);检查 :1789 中转是否就绪" % timeout)
    except Exception as e:  # noqa: BLE001
        raise SentinelError("sentinel runner 异常: %s" % e)
    if proc.returncode != 0:
        raise SentinelError("sentinel runner 非零退出(%s): %s"
                            % (proc.returncode, (proc.stderr or "")[:200]))
    out = (proc.stdout or "").strip()
    if not out:
        raise SentinelError("sentinel runner 空输出(:1789 中转可能没过 CF)")
    return out


def gen_sentinel_pair(device_id, flow, user_agent, page_url=None,
                      language="en-US", languages="en-US,en;q=0.9",
                      width=1920, height=1080, cores=8,
                      timezone="America/Los_Angeles", timeout=120):
    """返回 (sentinel_token, so_token);so 可能为 None(不是每个 flow 都要 SO)。"""
    import json as _json
    out = _run_runner(device_id, flow, user_agent, page_url, language, languages,
                      width, height, cores, timezone, timeout, with_so=True)
    try:
        env = _json.loads(out)
    except Exception:
        return out, None
    token = env.get("token")
    if not token:
        raise SentinelError("runner 返回信封里没有 token: %s" % out[:200])
    return token, (env.get("so") or None)


def gen_sentinel_token(device_id, flow, user_agent, page_url=None,
                       language="en-US", languages="en-US,en;q=0.9",
                       width=1920, height=1080, cores=8,
                       timezone="America/Los_Angeles", timeout=60):
    """返回 token 字符串;失败一律 raise SentinelError。"""
    return _run_runner(device_id, flow, user_agent, page_url, language, languages,
                       width, height, cores, timezone, timeout, with_so=False)


_SENTINEL_MAX_ATTEMPTS = 3
_SENTINEL_BACKOFF_BASE = 0.8


def gen_sentinel_pair_retry(*args, log=None, attempts=_SENTINEL_MAX_ATTEMPTS, **kwargs):
    """带重试的 gen_sentinel_pair。全部尝试都失败才 raise。"""
    errs = []
    for i in range(max(1, attempts)):
        try:
            return gen_sentinel_pair(*args, **kwargs)
        except SentinelError as e:
            errs.append(str(e)[:120])
            if i == attempts - 1:
                break
            delay = _SENTINEL_BACKOFF_BASE * (2 ** i)
            if log:
                log("    sentinel: 第 %d/%d 次失败(%s),%.1fs 后重试" % (i + 1, attempts, str(e)[:60], delay))
            time.sleep(delay)
    raise RuntimeError("sentinel %d 次尝试均失败: %s" % (attempts, " | ".join(errs)))


def ensure_sentinel_proxy(exit_proxy: str = "", ready_timeout: float = 15.0):
    """确保本地 sentinel 中转(:1789)在跑。已在跑则返回 None;否则起一个并轮询就绪。

    :param exit_proxy: sentinel/req 的出口代理(socks5://...)。空 = 直连。
        建议传与主流程同一条住宅出口,消除 sentinel 走机房 IP 与住宅业务 origin 不一致的关联信号。
    """
    import sys
    import requests as _rq
    try:
        _rq.get("http://127.0.0.1:1789/", timeout=2)
        return None
    except Exception:
        pass
    env = os.environ.copy()
    if exit_proxy:
        env["OAI_SENTINEL_EXIT"] = exit_proxy
    p = subprocess.Popen(
        [sys.executable, "-X", "utf8", _PROXY_SERVER],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            _rq.get("http://127.0.0.1:1789/", timeout=1)
            return p
        except Exception:
            continue
    return p

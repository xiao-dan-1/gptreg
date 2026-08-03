"""Node VM 跑官方 sdk.js 产**真 t**（协议产真 t 突破，见 capture/protocol-real-t-20260803/）。

无需浏览器。在 Node vm 沙箱里真实执行 sdk.js 的 turnstile 求解器 `_n`，
产出与浏览器同源的 turnstile token（~900+ 字符）。

四个必要条件（详见 capture/protocol-real-t-20260803/README.md）：
  1. navigator 用 Object.defineProperty 覆盖（Node 内置 getter-only，否则指纹 UA 退化）
  2. D(challenge, request_p) 注册解码器密钥
  3. setTimeout 不可同步（否则 500ms 看门狗立即触发 → "0"）
  4. `_n` 的 500ms 超时提到 60s+（解释器在 vm 里慢）

用法:
    token = get_sentinel_token_via_quickjs(session, device_id, flow="oauth_create_account", cfg=cfg)
    # token = '{"p":"gAAAAAB...", "t":"SRQZ...", "c":"...", "id":"...", "flow":"..."}'
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"

_WRAPPER_JS = r"""
const fs=require('fs');
const tm=Number(process.env.QJS_TIMEOUT_MS||'90000');
const sdkFile=process.env.QJS_SDK_FILE, scriptFile=process.env.QJS_SCRIPT;
let input='';process.stdin.setEncoding('utf8');
process.stdin.on('data',c=>{input+=c});
process.stdin.on('end',async()=>{try{
  const payload=JSON.parse(input||'{}');
  globalThis.__payload_json=JSON.stringify(payload);
  globalThis.__sdk_source=fs.readFileSync(sdkFile,'utf8');
  globalThis.__vm_done=false;globalThis.__vm_output_json='';globalThis.__vm_error='';
  eval(fs.readFileSync(scriptFile,'utf8'));
  const st=Date.now();
  while(!globalThis.__vm_done){
    if(Date.now()-st>tm){throw new Error('quickjs vm timeout');}
    await new Promise(r=>setTimeout(r,1));
  }
  if(String(globalThis.__vm_error||'').trim())throw new Error(String(globalThis.__vm_error));
  process.stdout.write(String(globalThis.__vm_output_json||''));
}catch(e){process.stderr.write((e&&e.stack||String(e)));process.exit(1);}});
""".strip()


def _node_binary() -> str:
    return (os.environ.get("NODE_EXECUTABLE", "") or "").strip() or "node"


def _quickjs_script() -> Path:
    return Path(__file__).resolve().parent.parent / "vendor" / "sentinel" / "openai_sentinel_quickjs.js"


def _ensure_sdk(session: Any, sv: str, timeout_ms: int) -> Path:
    """下载/缓存当前 sdk.js。"""
    cache = Path(tempfile.gettempdir()) / "openai-sentinel-demo" / sv / "sdk.js"
    if cache.exists() and cache.stat().st_size > 0:
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://sentinel.openai.com/sentinel/{sv}/sdk.js"
    resp = session.get(url, timeout=max(10, int(timeout_ms / 1000)))
    if resp.status_code != 200:
        raise RuntimeError(f"下载 sdk.js 失败: HTTP {resp.status_code}")
    content = getattr(resp, "content", b"") or (resp.text or "").encode()
    if not content:
        raise RuntimeError("下载 sdk.js 失败: 响应为空")
    cache.write_bytes(content)
    return cache


def _run_action(script: Path, sdk_file: Path, action: str, payload: dict, timeout_ms: int) -> dict:
    body = dict(payload)
    body["action"] = action
    proc = subprocess.run(
        [_node_binary(), "-e", _WRAPPER_JS],
        input=json.dumps(body, ensure_ascii=False),
        text=True, capture_output=True, encoding="utf-8", errors="replace",
        timeout=max(30, int(timeout_ms / 1000) + 10),
        env={
            **os.environ,
            "QJS_SDK_FILE": str(sdk_file),
            "QJS_SCRIPT": str(script),
            "QJS_TIMEOUT_MS": str(timeout_ms),
        },
    )
    if proc.returncode != 0:
        raise RuntimeError(f"quickjs {action} 失败: {(proc.stderr or proc.stdout or 'unknown').strip()[:300]}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError(f"quickjs {action} 返回空输出")
    return json.loads(out)


def _fingerprint_payload(cfg: dict[str, Any], device_id: str, sv: str) -> dict:
    """真实浏览器指纹（与 config.browser 一致），供 installRuntime 用。"""
    b = cfg.get("browser", {}) or {}
    languages = str(b.get("languages", "en-US,en") or "en-US,en").split(",")
    return {
        "device_id": device_id,
        "user_agent": b.get("user_agent", ""),
        "screen_width": int(b.get("screen_width", 1920)),
        "screen_height": int(b.get("screen_height", 1080)),
        "hardware_concurrency": int(b.get("hardware_concurrency", 16)),
        "language": b.get("language", "en-US"),
        "languages": [x.strip() for x in languages if x.strip()] or ["en-US"],
        "performance_now": round(time.time() * 1000 % 50000, 2),
        "time_origin": round(time.time() * 1000, 1),
        "script_src": f"https://sentinel.openai.com/sentinel/{sv}/sdk.js",
    }


def get_sentinel_token_via_quickjs(
    session: Any,
    device_id: str,
    *,
    flow: str,
    cfg: dict[str, Any] | None = None,
    timeout_ms: int = 90000,
    log=None,
) -> str:
    """返回完整 sentinel-token JSON 字符串（含真 t）。"""
    log = log or (lambda m: logger.info(m))
    cfg = cfg or {}
    protocol = cfg.get("protocol", {}) or {}
    sv = str(protocol.get("sentinel_sv") or "20260219f9f6")

    script = _quickjs_script()
    if not script.exists():
        raise FileNotFoundError(f"quickjs 适配器不存在: {script}")

    sdk_file = _ensure_sdk(session, sv, timeout_ms)

    # 1) requirements：SDK 自己的 getRequirementsToken()（真实指纹）
    fp = _fingerprint_payload(cfg, device_id, sv)
    req = _run_action(script, sdk_file, "requirements", fp, timeout_ms)
    request_p = str(req.get("request_p") or "")
    if not request_p:
        raise RuntimeError("quickjs requirements 未返回 request_p")

    # 2) /req 拿 challenge（走 session 代理）
    body = json.dumps({"p": request_p, "id": device_id, "flow": flow}, separators=(",", ":"))
    resp = session.post(
        SENTINEL_REQ_URL, data=body,
        headers=session.sentinel_headers(), timeout=max(10, int(timeout_ms / 1000)),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"quickjs /req HTTP {resp.status_code}: {(resp.text or '')[:200]}")
    challenge = resp.json()
    c_value = str(challenge.get("token") or "")
    if not c_value:
        raise RuntimeError("quickjs /req 返回 token 为空")

    # 3) solve：getEnforcementToken + D 注册 + _n 产真 t
    t0 = time.time()
    solved = _run_action(script, sdk_file, "solve", {
        "device_id": device_id,
        "request_p": request_p,
        "challenge": challenge,
        "flow": flow,
    }, timeout_ms)
    elapsed = time.time() - t0
    final_p = str(solved.get("final_p") or "")
    t = str(solved.get("t") or "")
    if not final_p or not t:
        raise RuntimeError(f"quickjs solve 返回不完整: p={len(final_p)} t={len(t)}")
    if t == "0" or "SyntaxError" in t or t.startswith("MDogU3ludGF4"):
        raise RuntimeError(f"quickjs solve 产假 t: {t[:40]}")

    token = json.dumps(
        {"p": final_p, "t": t, "c": c_value, "id": device_id, "flow": flow},
        separators=(",", ":"), ensure_ascii=False,
    )
    log(f"quickjs 真 t 成功 (t_len={len(t)} elapsed={elapsed:.0f}s)")
    return token

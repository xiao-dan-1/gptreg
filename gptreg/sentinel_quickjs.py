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
import random
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 2026-08 知己知彼：浏览器 SDK 实际从 chatgpt.com/backend-api/sentinel/req 取 challenge
# （frame.html iframe 上下文），不是 sentinel.openai.com。复刻浏览器请求头。
SENTINEL_REQ_URL = "https://chatgpt.com/backend-api/sentinel/req"

_WRAPPER_JS = r"""
const fs=require('fs');
// 在 eval(adapter) 之前捕获 Node 真实 setTimeout——vm 会覆盖 globalThis.setTimeout 为同步版，
// 若轮询用它会形成微任务忙碌循环、饿死真实 macrotask 定时器（collector/so 异步全挂起的根因）。
const __rST=setTimeout.bind(null),__rCST=clearTimeout.bind(null);
const tm=Number(process.env.QJS_TIMEOUT_MS||'120000');
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
    await new Promise(r=>__rST(r,1));  // 真实定时器，让事件循环跑起来（macrotask 不被饿死）
  }
  if(String(globalThis.__vm_error||'').trim())throw new Error(String(globalThis.__vm_error));
  // 根因级性能修复：_n 内部注册 120s 看门狗 timer，若不主动 exit，Node 会等 timer 到点才
  // 自然退出 → 每次 solve 白等 120s。同步写 stdout 后立即 exit。
  const __out=String(globalThis.__vm_output_json||'');
  try{fs.writeSync(1,__out)}catch(e){process.stdout.write(__out)}
  process.exit(0);
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


def _run_action(
    script: Path, sdk_file: Path, action: str, payload: dict, timeout_ms: int,
    env_extra: dict[str, str] | None = None,
) -> dict:
    body = dict(payload)
    body["action"] = action
    env = {
        **os.environ,
        "QJS_SDK_FILE": str(sdk_file),
        "QJS_SCRIPT": str(script),
        "QJS_TIMEOUT_MS": str(timeout_ms),
    }
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    proc = subprocess.run(
        [_node_binary(), "-e", _WRAPPER_JS],
        input=json.dumps(body, ensure_ascii=False),
        text=True, capture_output=True, encoding="utf-8", errors="replace",
        timeout=max(30, int(timeout_ms / 1000) + 10),
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"quickjs {action} 失败: {(proc.stderr or proc.stdout or 'unknown').strip()[:300]}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError(f"quickjs {action} 返回空输出")
    return json.loads(out)


def _fingerprint_payload(cfg: dict[str, Any], device_id: str, sv: str) -> dict:
    """真实浏览器指纹（与 config.browser 一致），供 installRuntime 用。

    time_origin 由调用方按「一次注册」计算一次、两次动作（requirements/solve）
    复用同一值 —— 真浏览器 timeOrigin 是页面加载常数（A3 修复）。
    """
    b = cfg.get("browser", {}) or {}
    languages = str(b.get("languages", "en-US,en") or "en-US,en").split(",")
    # 页面加载到产 token 的真实耗时（真浏览器实测 5-12s），性能时钟从该偏移起算。
    # 一次注册固定（requirements/solve 复用同一 fp）。
    elapsed = int(b.get("page_elapsed_ms", 0) or 0) or random.randint(3000, 15000)
    return {
        "device_id": device_id,
        "user_agent": b.get("user_agent", ""),
        "screen_width": int(b.get("screen_width", 1920)),
        "screen_height": int(b.get("screen_height", 1080)),
        "hardware_concurrency": int(b.get("hardware_concurrency", 16)),
        "device_memory": int(b.get("device_memory", 16) or 16),
        "max_touch_points": int(b.get("max_touch_points", 10) or 10),
        "language": b.get("language", "en-US"),
        "languages": [x.strip() for x in languages if x.strip()] or ["en-US"],
        "time_origin": round(time.time() * 1000 - elapsed, 1),
        "page_elapsed_ms": elapsed,
        # 真浏览器 auth.openai.com 的 localStorage 有 Statsig 键（指纹计算 Object.keys(localStorage) 读取）
        "statsig_id": str(b.get("statsig_id", "444584300") or "444584300"),
        "statsig_stable_id": str(__import__("uuid").uuid4()),
        "statsig_session_id": str(__import__("uuid").uuid4()),
        "js_heap_size_limit": int(b.get("js_heap_size_limit", 4395630592) or 4395630592),
        # TZ 默认空 = 用机器时区（与本机 browser_sentinel 一致）；设 cfg.browser.timezone 可对齐代理地区
        "timezone": str(b.get("timezone", "") or ""),
        # 复刻真页面：script_src=backend-api 加载器；sdk_url=动态加载的版本化 SDK
        "script_src": f"https://sentinel.openai.com/backend-api/sentinel/sdk.js",
        "sdk_url": f"https://sentinel.openai.com/sentinel/{sv}/sdk.js",
    }


def _seed_extra() -> dict[str, Any]:
    """QJS_SEED=1 时读 data/seed_quickjs.json，把浏览器采集的种子注入 solve payload。

    种子 = 真浏览器一次性采集的 __reactRouterContext / localStorage / 字体测量真值，
    用于「种子 + 重算」验证：补全 vm 缺的静态环境值，测是否改善 t 保真度/存活。
    """
    if os.environ.get("QJS_SEED") != "1":
        return {}
    p = Path(__file__).resolve().parent.parent / "data" / "seed_quickjs.json"
    if not p.exists():
        logger.warning("QJS_SEED=1 但 data/seed_quickjs.json 不存在")
        return {}
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        extra: dict[str, Any] = {}
        if isinstance(s.get("react_router_full"), dict):
            extra["react_router_full"] = s["react_router_full"]
        if isinstance(s.get("ls_extra"), dict):
            extra["ls_extra"] = s["ls_extra"]
        if isinstance(s.get("font_gbcr"), dict):
            extra["font_gbcr"] = s["font_gbcr"]
        return extra
    except Exception:
        logger.warning("解析 seed_quickjs.json 失败", exc_info=True)
        return {}


def _dump_solved_tokens(
    device_id: str, request_p: str, final_p: str, t: str, so_header: str | None, flow: str
) -> None:
    """dump 真实产出的 token 到 data/solved_tokens/，供解码验证跨 token 自洽性（A2/A3）。

    requirements 与 solve 是两次独立 Node 进程，_fingerprint_payload 里
    performance_now/time_origin 每次现算——真浏览器 timeOrigin 是页面加载常数。
    解码对比可实证该漂移是否存在。
    """
    try:
        out_dir = Path(__file__).resolve().parent.parent / "data" / "solved_tokens"
        out_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": round(time.time(), 3),
            "device_id": device_id,
            "flow": flow,
            "request_p": request_p,
            "final_p": final_p,
            "t_len": len(t),
            "so": so_header,
        }
        with open(out_dir / "solved_tokens.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("dump solved tokens 失败", exc_info=True)


def get_sentinel_token_via_quickjs(
    session: Any,
    device_id: str,
    *,
    flow: str,
    cfg: dict[str, Any] | None = None,
    timeout_ms: int = 120000,
    log=None,
) -> tuple[str, str | None]:
    """返回 (sentinel-token JSON 字符串（含真 t）, so-header 或 None)。"""
    log = log or (lambda m: logger.info(m))
    cfg = cfg or {}
    protocol = cfg.get("protocol", {}) or {}
    sv = str(protocol.get("sentinel_sv") or "20260219f9f6")

    script = _quickjs_script()
    if not script.exists():
        raise FileNotFoundError(f"quickjs 适配器不存在: {script}")

    sdk_file = _ensure_sdk(session, sv, timeout_ms)

    # 1) requirements：SDK 自己的 getRequirementsToken()（真实指纹）
    #    fp 一次注册算一次，requirements 与 solve 复用同一份（含 time_origin），
    #    否则 solve 用默认指纹 UA/screen/memory 与 requirements 不一致（保真 bug）。
    fp = _fingerprint_payload(cfg, device_id, sv)
    tz = str(fp.get("timezone") or "").strip()
    env_extra = {"TZ": tz} if tz else {}
    req = _run_action(script, sdk_file, "requirements", fp, timeout_ms, env_extra=env_extra)
    request_p = str(req.get("request_p") or "")
    if not request_p:
        raise RuntimeError("quickjs requirements 未返回 request_p")

    # 2) /req 拿 challenge（走 session 代理，复刻浏览器 frame 上下文）
    body = json.dumps({"p": request_p, "id": device_id, "flow": flow}, separators=(",", ":"))
    resp = session.post(
        SENTINEL_REQ_URL, data=body,
        headers={
            "content-type": "text/plain;charset=UTF-8",
            "referer": "https://chatgpt.com/backend-api/sentinel/frame.html",
            "origin": "https://chatgpt.com",
            "user-agent": session.user_agent,
            "accept": "*/*",
        },
        timeout=max(10, int(timeout_ms / 1000)),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"quickjs /req HTTP {resp.status_code}: {(resp.text or '')[:200]}")
    challenge = resp.json()
    c_value = str(challenge.get("token") or "")
    if not c_value:
        raise RuntimeError("quickjs /req 返回 token 为空")

    # 3) solve：getEnforcementToken + D 注册 + _n 产真 t
    #    关键：solve 复用同一份 fp（含 time_origin / UA / screen / memory），
    #    与 requirements 同页面加载 → 两 token 指纹一致（A2/A3 保真修复）。
    t0 = time.time()
    solve_payload = dict(fp)
    solve_payload.update({
        "request_p": request_p,
        "challenge": challenge,
        "flow": flow,
    })
    # 种子注入（QJS_SEED=1 时）：浏览器采集的 rctx/localStorage/字体真值
    seed = _seed_extra()
    if seed:
        solve_payload.update(seed)
        logger.info("[quickjs] 注入种子 %s", list(seed.keys()))
    # 行为字段注入（QJS_INJECT=1 时）：等 collector 完成后注入 __oai_so_* 字段，
    # 让 vm so 编码真实行为值（纯协议存活关键：vm so 行为字段空必死）
    if os.environ.get("QJS_SO_WAIT"):
        solve_payload["so_wait_collector_ms"] = int(os.environ.get("QJS_SO_WAIT", "1000"))
    if os.environ.get("QJS_INJECT") == "1":
        solve_payload["inject_oai_so"] = True
        logger.info("[quickjs] 注入 __oai_so_* 行为字段（QJS_INJECT=1）")
    if os.environ.get("QJS_PATCH") == "1":
        solve_payload["patch_oai_so"] = True
        solve_payload["so_wait_collector_ms"] = int(os.environ.get("QJS_SO_WAIT", "800"))
        logger.info("[quickjs] 补 browser 基线字段绕过 SUBRUN 污染（QJS_PATCH=1）")
    if os.environ.get("QJS_SNAP_INJECT") == "1":
        solve_payload["snap_inject"] = True
        logger.info("[quickjs] snapshot 读取点注入自然字段值（QJS_SNAP_INJECT=1，第一性原理攻法）")
    if os.environ.get("QJS_SNAP_EXTREME") == "1":
        solve_payload["snap_extreme"] = True
        logger.info("[quickjs] 黑盒探测：极端字段值注入（QJS_SNAP_EXTREME=1）")
    solved = _run_action(script, sdk_file, "solve", solve_payload, timeout_ms, env_extra=env_extra)
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
    # so：从 solve 输出提取，组装 openai-sentinel-so-token
    # QJS_SO_TEMPLATE 指向 JSON {so_val: "..."}：用预采集 browser 真 so_val 替代 vm 假 so
    # （vm snapshot_dx 抛 TypeError 产假 so，控制变量实验证明假 so 必死、真 so 配 vm t 能活）。
    so_header: str | None = None
    so_raw = solved.get("so")
    _so_tpl = os.environ.get("QJS_SO_TEMPLATE", "").strip()
    if _so_tpl:
        try:
            _td = json.loads(Path(_so_tpl).read_text(encoding="utf-8"))
            _tval = str(_td.get("so_val") or "")
            if _tval:
                so_header = json.dumps(
                    {"so": _tval, "c": c_value, "id": device_id, "flow": flow},
                    separators=(",", ":"), ensure_ascii=False,
                )
                logger.info("[quickjs] 用模板 so_val(len=%s) 替代 vm 假 so", len(_tval))
        except Exception as exc:
            logger.warning("QJS_SO_TEMPLATE 读取失败: %s", exc)
    if so_header is None and so_raw:
        so_val = so_raw
        if isinstance(so_raw, str):
            try:
                sp = json.loads(so_raw)
                if isinstance(sp, dict) and sp.get("so"):
                    so_val = sp["so"]
            except Exception:
                pass
        so_header = json.dumps(
            {"so": so_val, "c": c_value, "id": device_id, "flow": flow},
            separators=(",", ":"), ensure_ascii=False,
        )
    log(f"quickjs 真 t 成功 (t_len={len(t)} so_len={len(so_header) if so_header else 0} elapsed={elapsed:.0f}s)")
    _dump_solved_tokens(device_id, request_p, final_p, t, so_header, flow)
    return token, so_header

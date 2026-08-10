#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 so 纯程序获取：vm(quickjs) 跑 sdk.js 能否产出有效 so。

对比 turb-gpt 的"Node vm 跑官方 sdk.js 自己算 so"：
- 我们的 quickjs 适配器已内置 sessionObserverToken() 调用 + 多种行为字段注入策略
- 本脚本诊断各策略下 so 的产出质量（值长度 / 是否假值 / 错误栈 / oai_so 字段状态）

用法:
    python capture/research/probe_vm_so.py [--rounds N] [--variant all|default|snap|patch|inject]
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.sentinel_quickjs import (  # noqa: E402
    _fingerprint_payload,
    _quickjs_script,
    _run_action,
    _ensure_sdk,
    SENTINEL_REQ_URL,
)

FLOW = "oauth_create_account"

# 假 so 特征：SyntaxError / MDogU3ludGF4(base64 的 "M Dog Syntax"?)
def _is_fake_so(so: str) -> bool:
    s = so or ""
    return "SyntaxError" in s or s.startswith("MDogU3ludGF4")


def _describe_so(so):
    if not so:
        return "NONE"
    n = len(so)
    if _is_fake_so(so):
        return f"FAKE len={n} head={so[:60]!r}"
    return f"REAL? len={n} head={so[:60]!r}"


def run_variant(cfg, sess, device_id, variant: str, env_extra: dict) -> dict:
    """跑一轮 quickjs requirements→/req→solve，返回完整 solve 诊断。"""
    from gptreg.sentinel_quickjs import _quickjs_script, _ensure_sdk, _fingerprint_payload

    protocol = cfg.get("protocol") or {}
    sv = str(protocol.get("sentinel_sv") or "20260219f9f6")
    script = _quickjs_script()
    sdk_file = _ensure_sdk(sess, sv, 120000)
    fp = _fingerprint_payload(cfg, device_id, sv)

    t0 = time.time()
    req = _run_action(script, sdk_file, "requirements", fp, 120000, env_extra=env_extra)
    request_p = str(req.get("request_p") or "")
    if not request_p:
        return {"variant": variant, "error": "requirements 无 request_p"}

    body = json.dumps({"p": request_p, "id": device_id, "flow": FLOW}, separators=(",", ":"))
    resp = sess.post(
        SENTINEL_REQ_URL, data=body,
        headers={
            "content-type": "text/plain;charset=UTF-8",
            "referer": "https://chatgpt.com/backend-api/sentinel/frame.html",
            "origin": "https://chatgpt.com",
            "user-agent": sess.user_agent,
            "accept": "*/*",
        },
        timeout=120,
    )
    if resp.status_code != 200:
        return {"variant": variant, "error": f"/req HTTP {resp.status_code}: {(resp.text or '')[:150]}"}
    challenge = resp.json()

    solve_payload = dict(fp)
    solve_payload.update({"request_p": request_p, "challenge": challenge, "flow": FLOW})
    # 各变体的注入策略，与 sentinel_quickjs.get_sentinel_token_via_quickjs 的 QJS_ 环境变量对齐
    if variant == "snap":
        solve_payload["snap_inject"] = True
    elif variant == "extreme":
        solve_payload["snap_extreme"] = True
    elif variant == "patch":
        solve_payload["patch_oai_so"] = True
        solve_payload["so_wait_collector_ms"] = int(os.environ.get("QJS_SO_WAIT", "800"))
    elif variant == "inject":
        solve_payload["inject_oai_so"] = True
    elif variant == "wait":
        solve_payload["so_wait_collector_ms"] = 1500

    solved = _run_action(script, sdk_file, "solve", solve_payload, 150000, env_extra=env_extra)
    elapsed = time.time() - t0

    final_p = str(solved.get("final_p") or "")
    t = str(solved.get("t") or "")
    so = solved.get("so")
    if isinstance(so, dict):
        so = json.dumps(so, separators=(",", ":"))
    so = str(so or "")

    oai_so = solved.get("oai_so") or {}
    n_oai = len(oai_so) if isinstance(oai_so, dict) else 0
    return {
        "variant": variant,
        "elapsed_s": round(elapsed, 1),
        "t_len": len(t),
        "t_fake": t == "0" or "SyntaxError" in t,
        "so": _describe_so(so),
        "so_len": len(so),
        "so_error": (solved.get("so_error") or solved.get("so_jt_err") or solved.get("so_rej")) or None,
        "n_oai_fields": n_oai,
        "oai_sample": {k: v for k, v in list((oai_so or {}).items())[:8]} if isinstance(oai_so, dict) else {},
        "so_value": so,
    }


def main() -> int:
    args = sys.argv[1:]
    variant = "all"
    if "--variant" in args:
        variant = args[args.index("--variant") + 1]
    variants = {
        "all": ["default", "wait", "snap", "patch", "inject", "extreme"],
        "default": ["default"],
        "wait": ["wait"],
        "snap": ["snap"],
        "patch": ["patch"],
        "inject": ["inject"],
        "extreme": ["extreme"],
    }.get(variant, ["default"])

    cfg = load_config("config.yaml")
    resolved = resolve_proxy(cfg)
    proxy = resolved.session_url
    print(f"代理: {proxy}\n")

    sess = BrowserSession(cfg, proxy=proxy)
    device_id = str(uuid.uuid4())
    results = []
    for v in variants:
        print(f"=== variant: {v} ===", flush=True)
        try:
            r = run_variant(cfg, sess, device_id, v, {})
            results.append(r)
            for k in ("variant", "elapsed_s", "t_len", "t_fake", "so", "so_len", "n_oai_fields"):
                print(f"  {k}: {r.get(k)}", flush=True)
            if r.get("so_error"):
                print(f"  so_error: {(r.get('so_error') or '')[:300]}", flush=True)
            if r.get("oai_sample"):
                print(f"  oai_sample: {json.dumps(r.get('oai_sample'), ensure_ascii=False)}", flush=True)
        except Exception as exc:
            print(f"  variant {v} 异常: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            results.append({"variant": v, "error": f"{type(exc).__name__}: {str(exc)[:150]}"})
        print(flush=True)

    # 汇总 + 存 so 值供后续与 browser so 对比
    print("==== 汇总 ====")
    for r in results:
        print(f"  {r.get('variant'):8s} t_len={r.get('t_len')} {r.get('so')} oai={r.get('n_oai_fields')} {r.get('elapsed_s')}s")
    out_dir = Path(__file__).resolve().parent / "data_archive"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "vm_so_probe_20260810.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存: {out}")
    sess.close()
    resolved.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

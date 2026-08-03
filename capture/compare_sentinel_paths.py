#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""零耗号：对照三条 sentinel 路径（同 device_id / flow）。

路径:
  1) pow  — 当前注册主路径 SentinelPoW 纯 Python（t=\"\"，无 so）
  2) file — Node runner + Python 预拉 challenge（旧）
  3) url  — Node runner + 本地 1789 闭环（k12）

不碰号池、不 create_account。输出 JSON + Markdown 表。
"""
from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gptreg.config import load_config
from gptreg.proxyutil import resolve_proxy
from gptreg.sentinel import (
    SentinelPoW,
    build_so_header,
    generate_requirements_token,
    generate_sentinel_token_via_node,
)
from gptreg.sentinel_proxy import ensure_sentinel_proxy, stop_sentinel_proxy
from gptreg.session import BrowserSession


FLOW = "oauth_create_account"
OUT_DIR = Path(__file__).resolve().parent / f"sentinel-path-compare-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _t_meta(t: str) -> dict:
    raw = t or ""
    decoded_head = ""
    is_syntax = False
    try:
        if raw:
            # t 可能是 base64；失败则当明文
            pad = "=" * ((4 - len(raw) % 4) % 4)
            try:
                decoded = base64.b64decode(raw + pad, validate=False)
                try:
                    decoded_head = decoded.decode("utf-8", errors="replace")[:160]
                except Exception:
                    decoded_head = repr(decoded[:80])
            except Exception:
                decoded_head = raw[:160]
        is_syntax = "SyntaxError" in (decoded_head or "") or "SyntaxError" in raw
    except Exception as exc:
        decoded_head = f"<decode_err:{exc}>"
    return {
        "t_len": len(raw),
        "t_is_syntaxerror": is_syntax,
        "t_decoded_head": decoded_head,
        "t_empty": raw == "",
    }


def _analyze_token(token_text: str, device_id: str, flow: str, mode: str) -> dict:
    out: dict = {
        "mode": mode,
        "ok": False,
        "keys": [],
        "has_so": False,
        "so_len": 0,
        "so_header_len": 0,
        "p_len": 0,
        "c_len": 0,
        "error": None,
    }
    try:
        data = json.loads(token_text)
    except Exception as exc:
        out["error"] = f"json_parse: {exc}"
        out["raw_head"] = (token_text or "")[:200]
        return out

    out["ok"] = True
    out["keys"] = list(data.keys())
    out["p_len"] = len(str(data.get("p") or ""))
    out["c_len"] = len(str(data.get("c") or ""))
    so_val = data.get("so")
    if isinstance(so_val, str) and so_val:
        fake = "SyntaxError" in so_val or so_val.startswith("MDogU3ludGF4")
        out["has_so"] = not fake
        out["so_len"] = len(so_val)
        out["so_is_fake"] = fake
    else:
        out["has_so"] = False
        out["so_len"] = 0
        out["so_is_fake"] = False

    t_meta = _t_meta(str(data.get("t") or ""))
    out.update(t_meta)

    so_header = build_so_header(token_text, device_id, flow, "")
    if so_header and ("SyntaxError" in so_header or "MDogU3ludGF4" in so_header):
        so_header = None
    out["so_header_len"] = len(so_header or "")
    out["so_header_present"] = bool(so_header)
    return out


def _challenge_meta(challenge: dict | None) -> dict:
    if not challenge:
        return {"present": False}
    so = challenge.get("so") or {}
    pow_ = challenge.get("proofofwork") or {}
    ts = challenge.get("turnstile") or {}
    return {
        "present": True,
        "keys": list(challenge.keys()),
        "so_required": bool(so.get("required")),
        "pow_required": bool(pow_.get("required")),
        "turnstile_required": bool(ts.get("required")),
        "has_collector_dx": bool(so.get("collector_dx")),
        "has_snapshot_dx": bool(so.get("snapshot_dx")),
        "token_len": len(str(challenge.get("token") or "")),
    }


def run_pow(session: BrowserSession, device_id: str) -> dict:
    t0 = time.time()
    try:
        pow_engine = SentinelPoW(ua=session.user_agent)
        token = pow_engine.build(session.session, device_id, FLOW)
        result = _analyze_token(token, device_id, FLOW, "pow")
        result["elapsed_s"] = round(time.time() - t0, 3)
        result["note"] = "注册主路径：纯 Python FNV-1a，t 强制空串，无 Node/so"
        return result
    except Exception as exc:
        return {
            "mode": "pow",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.time() - t0, 3),
        }


def run_node_file(session: BrowserSession, cfg: dict, device_id: str) -> dict:
    t0 = time.time()
    challenge = None
    try:
        # 用与旧 request_sentinel 一致的 p 预拉
        p = generate_requirements_token(cfg, device_id)
        body = json.dumps({"p": p, "id": device_id, "flow": FLOW}, separators=(",", ":"))
        resp = session.session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            data=body,
            headers=session.sentinel_headers() if hasattr(session, "sentinel_headers") else {
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "https://sentinel.openai.com",
                "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
                "User-Agent": session.user_agent,
            },
            timeout=30,
        )
        challenge = resp.json() if resp.text else {}
        if resp.status_code != 200:
            raise RuntimeError(f"sentinel/req http={resp.status_code} body={(resp.text or '')[:200]}")
        token = generate_sentinel_token_via_node(
            cfg, challenge, FLOW, device_id, user_agent=session.user_agent
        )
        result = _analyze_token(token, device_id, FLOW, "file")
        result["elapsed_s"] = round(time.time() - t0, 3)
        result["challenge"] = _challenge_meta(challenge)
        result["note"] = "Node + Python 预拉 challenge（旧路径）"
        return result
    except Exception as exc:
        return {
            "mode": "file",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.time() - t0, 3),
            "challenge": _challenge_meta(challenge),
        }


def run_node_url(session: BrowserSession, cfg: dict, device_id: str) -> dict:
    t0 = time.time()
    try:
        challenge_url = ensure_sentinel_proxy(cfg)
        token = generate_sentinel_token_via_node(
            cfg,
            None,
            FLOW,
            device_id,
            user_agent=session.user_agent,
            challenge_url=challenge_url,
        )
        result = _analyze_token(token, device_id, FLOW, "url")
        result["elapsed_s"] = round(time.time() - t0, 3)
        result["challenge_url"] = challenge_url
        result["note"] = "Node + 本地 curl_cffi 中转闭环（k12 url）"
        return result
    except Exception as exc:
        return {
            "mode": "url",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.time() - t0, 3),
        }
    finally:
        try:
            stop_sentinel_proxy()
        except Exception:
            pass


def _md_table(results: list[dict]) -> str:
    headers = [
        "mode",
        "ok",
        "keys",
        "has_so",
        "so_len",
        "so_header_len",
        "t_len",
        "t_empty",
        "t_is_syntaxerror",
        "p_len",
        "c_len",
        "elapsed_s",
        "error",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for r in results:
        row = []
        for h in headers:
            v = r.get(h)
            if h == "keys" and isinstance(v, list):
                v = ",".join(v)
            if v is None:
                v = ""
            row.append(str(v).replace("|", "/").replace("\n", " ")[:80])
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    device_id = str(uuid.uuid4())
    resolved = resolve_proxy(cfg, None)
    session = BrowserSession(cfg, proxy=resolved.session_url)
    # 固定 device_id，便于对照
    session.device_id = device_id

    print(f"[compare] device_id={device_id}")
    print(f"[compare] flow={FLOW}")
    print(f"[compare] proxy={resolved.label()}")
    print(f"[compare] out={OUT_DIR}")

    results = []
    for name, fn in (
        ("pow", lambda: run_pow(session, device_id)),
        ("file", lambda: run_node_file(session, cfg, device_id)),
        ("url", lambda: run_node_url(session, cfg, device_id)),
    ):
        print(f"[compare] running {name} ...")
        r = fn()
        results.append(r)
        print(
            f"  -> ok={r.get('ok')} has_so={r.get('has_so')} t_len={r.get('t_len')} "
            f"t_syntax={r.get('t_is_syntaxerror')} err={r.get('error')}"
        )

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "device_id": device_id,
        "flow": FLOW,
        "proxy_label": resolved.label(),
        "registration_default": "pow",
        "results": results,
        "prior_closed_loop_note": (
            "历史 closed_loop_compare.json：file 假 t + 无 so；url 真 t 形态 + 仍无 so。"
            "当前注册主路径已切 SentinelPoW（pow），与 url 默认配置可能不同步。"
        ),
    }
    json_path = OUT_DIR / "compare.json"
    md_path = OUT_DIR / "COMPARE.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md = []
    md.append("# Sentinel 三路径对照（零耗号）")
    md.append("")
    md.append(f"- time: {payload['created_at']}")
    md.append(f"- device_id: `{device_id}`")
    md.append(f"- flow: `{FLOW}`")
    md.append(f"- proxy: `{payload['proxy_label']}`")
    md.append(f"- **注册主路径默认: `pow`（纯 Python）**")
    md.append("")
    md.append(_md_table(results))
    md.append("")
    md.append("## 判读规则")
    md.append("")
    md.append("1. `pow`：当前 `auth.make_sentinel_headers` 实际路径；预期 `t` 空、`has_so=false`。")
    md.append("2. `file`：旧 Node；常见 `t`=SyntaxError、`has_so=false`。")
    md.append("3. `url`：k12 闭环；`t` 可非 SyntaxError，但仍常 `has_so=false`。")
    md.append("4. **任何路径 has_so=true 且 so 非假值 → 值得立刻接 P1 存活实验。**")
    md.append("5. 三条全无 so → 确认 P0：浏览器真页产 so，勿再盲改 PoW/伪造 so。")
    md.append("")
    md.append("## 原始 JSON")
    md.append("")
    md.append(f"`{json_path}`")
    md.append("")
    md_path.write_text("\n".join(md), encoding="utf-8")

    print("\n" + "\n".join(md))
    resolved.close()
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

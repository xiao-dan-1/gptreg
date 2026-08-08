#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0 零耗号：真 Chrome 跑 SentinelSDK，导出 token + so（不注册、不耗号）。

策略（单变量）:
  - 用系统 Chrome（Playwright channel=chrome）
  - 打开 auth.openai.com 同源页，注入官方/本地 sdk.js
  - 调用 SentinelSDK.token(flow)
  - 解析 has_so / t 形态；假 so 丢弃
  - 默认连跑 3 次，写 JSON + MD

不改 gptreg 注册主路径。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gptreg.config import load_config
from gptreg.sentinel import build_so_header

FLOW_DEFAULT = "oauth_create_account"
PAGE_CANDIDATES = (
    "https://auth.openai.com/about-you",
    "https://auth.openai.com/",
    "https://chatgpt.com/",
)


def _t_meta(t: str) -> dict[str, Any]:
    raw = t or ""
    decoded_head = ""
    is_syntax = False
    try:
        if raw:
            pad = "=" * ((4 - len(raw) % 4) % 4)
            try:
                decoded = base64.b64decode(raw + pad, validate=False)
                decoded_head = decoded.decode("utf-8", errors="replace")[:160]
            except Exception:
                decoded_head = raw[:160]
        is_syntax = "SyntaxError" in (decoded_head or "") or "SyntaxError" in raw
    except Exception as exc:
        decoded_head = f"<decode_err:{exc}>"
    return {
        "t_len": len(raw),
        "t_empty": raw == "",
        "t_is_syntaxerror": is_syntax,
        "t_decoded_head": decoded_head,
    }


def analyze_token(token_text: str, device_id: str, flow: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "keys": [],
        "has_so": False,
        "so_len": 0,
        "so_is_fake": False,
        "so_header_len": 0,
        "so_header_present": False,
        "p_len": 0,
        "c_len": 0,
        "error": None,
    }
    try:
        data = json.loads(token_text)
    except Exception as exc:
        out["error"] = f"json_parse: {exc}"
        out["raw_head"] = (token_text or "")[:240]
        return out

    out["ok"] = True
    out["keys"] = list(data.keys())
    out["p_len"] = len(str(data.get("p") or ""))
    out["c_len"] = len(str(data.get("c") or ""))
    out.update(_t_meta(str(data.get("t") or "")))

    so_val = data.get("so")
    if isinstance(so_val, str) and so_val:
        fake = ("SyntaxError" in so_val) or so_val.startswith("MDogU3ludGF4")
        out["so_is_fake"] = fake
        out["has_so"] = not fake
        out["so_len"] = len(so_val)
    else:
        out["has_so"] = False
        out["so_len"] = 0

    so_header = build_so_header(token_text, device_id, flow, "")
    if so_header and ("SyntaxError" in so_header or "MDogU3ludGF4" in so_header):
        so_header = None
    out["so_header_present"] = bool(so_header)
    out["so_header_len"] = len(so_header or "")
    out["token_json"] = data  # 完整 token（研究用；勿当假 so 源）
    if so_header:
        out["so_header"] = so_header
    return out


def _pick_proxy(cfg: dict, proxy_arg: str | None) -> str | None:
    if proxy_arg is not None:
        p = proxy_arg.strip()
        return p or None
    # 浏览器侧优先 chain_via（7890），避免辣椒直连 403
    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    chain = (dyn.get("chain_via") or "").strip()
    if chain:
        return chain
    default = ((cfg.get("proxy") or {}).get("default") or "").strip()
    return default or None


def _sdk_url(cfg: dict) -> str:
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    return f"https://sentinel.openai.com/sentinel/{sv}/sdk.js"


def harvest_once(
    *,
    playwright,
    cfg: dict,
    flow: str,
    proxy: str | None,
    headless: bool,
    page_url: str,
    timeout_ms: int,
    use_local_sdk: bool,
) -> dict[str, Any]:
    from playwright.sync_api import TimeoutError as PwTimeout

    device_id = str(uuid.uuid4())
    t0 = time.time()
    result: dict[str, Any] = {
        "device_id": device_id,
        "flow": flow,
        "page_url": page_url,
        "proxy": proxy or "direct",
        "headless": headless,
    }

    launch_kwargs: dict[str, Any] = {
        "channel": "chrome",
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            f"--lang={(cfg.get('browser') or {}).get('language') or 'en-US'}",
        ],
    }
    # Playwright proxy: server only (no auth on chain_via 7890)
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}

    browser = playwright.chromium.launch(**launch_kwargs)
    try:
        context = browser.new_context(
            user_agent=(cfg.get("browser") or {}).get("user_agent") or None,
            locale=(cfg.get("browser") or {}).get("language") or "en-US",
            viewport={
                "width": int((cfg.get("browser") or {}).get("screen_width") or 1920),
                "height": int((cfg.get("browser") or {}).get("screen_height") or 1080),
            },
        )
        # oai-did 贯穿
        for domain in (".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"):
            try:
                context.add_cookies(
                    [
                        {
                            "name": "oai-did",
                            "value": device_id,
                            "domain": domain if domain.startswith(".") else domain,
                            "path": "/",
                        }
                    ]
                )
            except Exception:
                pass

        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        nav_err = None
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            nav_err = f"{type(exc).__name__}: {exc}"
        result["final_url"] = page.url
        result["nav_error"] = nav_err

        # 轻微真实交互，给 sessionObserver 一点行为（不伪造 so 字符串）
        try:
            page.mouse.move(120, 160)
            page.mouse.move(420, 280, steps=8)
            page.mouse.wheel(0, 200)
            page.wait_for_timeout(400)
            page.keyboard.press("Tab")
            page.wait_for_timeout(200)
        except Exception:
            pass

        sdk_local = ROOT / "vendor" / "sentinel" / "sdk.js"
        load_mode = "url"
        try:
            if use_local_sdk and sdk_local.exists():
                page.add_script_tag(path=str(sdk_local))
                load_mode = "local_file"
            else:
                page.add_script_tag(url=_sdk_url(cfg))
                load_mode = "remote_url"
            page.wait_for_timeout(500)
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"sdk_load: {type(exc).__name__}: {exc}"
            result["elapsed_s"] = round(time.time() - t0, 3)
            result["sdk_load_mode"] = load_mode
            return result

        result["sdk_load_mode"] = load_mode

        # 确认 SDK 暴露
        has_sdk = page.evaluate(
            """() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')"""
        )
        result["has_sentinel_sdk"] = bool(has_sdk)
        if not has_sdk:
            # 有的页面把 SDK 挂在别处；再试一次 remote
            try:
                page.add_script_tag(url=_sdk_url(cfg))
                page.wait_for_timeout(800)
                has_sdk = page.evaluate(
                    """() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')"""
                )
                result["has_sentinel_sdk"] = bool(has_sdk)
                result["sdk_load_mode"] = "remote_url_retry"
            except Exception as exc:
                result["sdk_retry_error"] = f"{type(exc).__name__}: {exc}"

        if not has_sdk:
            result["ok"] = False
            result["error"] = "SentinelSDK.token 未暴露（页面/CSP/加载失败）"
            result["elapsed_s"] = round(time.time() - t0, 3)
            return result

        # 再给一点交互时间
        try:
            page.mouse.move(500, 400, steps=6)
            page.wait_for_timeout(600)
        except Exception:
            pass

        # Jennifer: token JSON 无 so 字段；so 来自独立 openai-sentinel-so-token。
        # SDK 暴露 sessionObserverToken(flow) → {so,c,...}；须在 token()/行为采集之后取。
        try:
            # 预热 init（若有）
            page.evaluate(
                """async (flow) => {
                    try {
                      if (window.SentinelSDK && typeof window.SentinelSDK.init === 'function') {
                        await window.SentinelSDK.init(flow);
                      }
                    } catch (e) { return String(e); }
                    return null;
                }""",
                flow,
            )
            # 再交互一段时间，给 sessionObserver 采集窗口
            try:
                for _ in range(3):
                    page.mouse.move(100 + _ * 80, 150 + _ * 40, steps=5)
                    page.wait_for_timeout(350)
                page.mouse.wheel(0, 300)
                page.wait_for_timeout(500)
            except Exception:
                pass

            bundle = page.evaluate(
                """async (flow) => {
                    const out = {
                      sdk_keys: window.SentinelSDK ? Object.keys(window.SentinelSDK) : [],
                      token: null,
                      so: null,
                      token_err: null,
                      so_err: null,
                    };
                    try {
                      const t = await window.SentinelSDK.token(flow);
                      out.token = (typeof t === 'string') ? t : JSON.stringify(t);
                    } catch (e) {
                      out.token_err = String(e && e.stack || e);
                    }
                    // 行为后再取 so
                    try {
                      for (let i = 0; i < 5; i++) {
                        await new Promise(r => setTimeout(r, 400));
                      }
                      if (typeof window.SentinelSDK.sessionObserverToken === 'function') {
                        const s = await window.SentinelSDK.sessionObserverToken(flow);
                        if (s == null) out.so = null;
                        else out.so = (typeof s === 'string') ? s : JSON.stringify(s);
                      } else {
                        out.so_err = 'no sessionObserverToken API';
                      }
                    } catch (e) {
                      out.so_err = String(e && e.stack || e);
                    }
                    return out;
                }""",
                flow,
            )
        except PwTimeout as exc:
            result["ok"] = False
            result["error"] = f"token_timeout: {exc}"
            result["elapsed_s"] = round(time.time() - t0, 3)
            return result
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"token_eval: {type(exc).__name__}: {exc}"
            result["elapsed_s"] = round(time.time() - t0, 3)
            return result

        token_text = (bundle or {}).get("token") or ""
        so_raw = (bundle or {}).get("so")
        result["sdk_keys"] = (bundle or {}).get("sdk_keys")
        result["token_err"] = (bundle or {}).get("token_err")
        result["so_api_err"] = (bundle or {}).get("so_err")
        result["so_raw_type"] = type(so_raw).__name__
        result["so_raw_len"] = len(so_raw) if isinstance(so_raw, str) else 0
        result["so_raw_head"] = (so_raw[:120] if isinstance(so_raw, str) else str(so_raw))[:120]

        if not token_text:
            result["ok"] = False
            result["error"] = result.get("token_err") or "empty token"
            result["elapsed_s"] = round(time.time() - t0, 3)
            return result

        analyzed = analyze_token(token_text, device_id, flow)
        # 用 sessionObserverToken 结果补 so header（不依赖 token JSON 内 so 字段）
        so_header = None
        so_parsed = None
        if isinstance(so_raw, str) and so_raw.strip():
            try:
                so_parsed = json.loads(so_raw)
            except Exception:
                so_parsed = None
            fake = ("SyntaxError" in so_raw) or so_raw.startswith("MDogU3ludGF4")
            if fake:
                result["so_is_fake"] = True
            elif isinstance(so_parsed, dict) and so_parsed.get("so"):
                # 已是 {so,c,...} wrapper
                if "id" not in so_parsed:
                    so_parsed["id"] = device_id
                if "flow" not in so_parsed:
                    so_parsed["flow"] = flow
                so_header = json.dumps(so_parsed, separators=(",", ":"), ensure_ascii=False)
                analyzed["has_so"] = True
                analyzed["so_len"] = len(str(so_parsed.get("so") or ""))
                analyzed["so_is_fake"] = False
            elif isinstance(so_parsed, str) or (so_parsed is None and so_raw):
                # 纯 so 字符串
                c_val = ""
                try:
                    c_val = str((json.loads(token_text) or {}).get("c") or "")
                except Exception:
                    pass
                so_val = so_parsed if isinstance(so_parsed, str) else so_raw
                so_header = json.dumps(
                    {"so": so_val, "c": c_val, "id": device_id, "flow": flow},
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                analyzed["has_so"] = True
                analyzed["so_len"] = len(so_val)
                analyzed["so_is_fake"] = False

        if so_header and ("SyntaxError" in so_header or "MDogU3ludGF4" in so_header):
            so_header = None
            analyzed["has_so"] = False
            analyzed["so_is_fake"] = True

        analyzed["so_header_present"] = bool(so_header)
        analyzed["so_header_len"] = len(so_header or "")
        if so_header:
            analyzed["so_header"] = so_header
        result.update(analyzed)
        result["elapsed_s"] = round(time.time() - t0, 3)
        return result
    finally:
        try:
            browser.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Browser harvest sentinel token/so (no register)")
    ap.add_argument("-n", "--times", type=int, default=3, help="连续次数，默认 3")
    ap.add_argument("--flow", default=FLOW_DEFAULT)
    ap.add_argument("--proxy", default=None, help="覆盖代理；empty=直连")
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--headed", action="store_true", help="有头模式（默认 headless）")
    ap.add_argument("--page", default=None, help="起始 URL，默认 about-you")
    ap.add_argument("--timeout", type=int, default=45, help="单次导航/脚本超时秒")
    ap.add_argument("--local-sdk", action="store_true", help="优先注入本地 vendor/sentinel/sdk.js")
    ap.add_argument("--try-pages", action="store_true", help="失败时轮换 PAGE_CANDIDATES")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.no_proxy:
        proxy = None
    elif args.proxy is not None and args.proxy.strip().lower() in {"empty", "none", "direct", ""}:
        proxy = None
    else:
        proxy = _pick_proxy(cfg, args.proxy)

    out_dir = (
        Path(__file__).resolve().parent
        / f"browser-so-harvest-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要 playwright：pip install playwright && playwright install chrome", file=sys.stderr)
        return 2

    pages = [args.page] if args.page else [PAGE_CANDIDATES[0]]
    if args.try_pages and not args.page:
        pages = list(PAGE_CANDIDATES)

    print(f"[harvest] times={args.times} flow={args.flow} proxy={proxy or 'direct'}")
    print(f"[harvest] headless={not args.headed} out={out_dir}")
    print(f"[harvest] pages={pages}")

    results: list[dict[str, Any]] = []
    with sync_playwright() as p:
        for i in range(max(1, args.times)):
            page_url = pages[i % len(pages)]
            print(f"[harvest] #{i+1}/{args.times} page={page_url} ...")
            r = harvest_once(
                playwright=p,
                cfg=cfg,
                flow=args.flow,
                proxy=proxy,
                headless=not args.headed,
                page_url=page_url,
                timeout_ms=int(args.timeout) * 1000,
                use_local_sdk=bool(args.local_sdk),
            )
            # 若失败且允许轮换，同次再试其他 page（不额外计入 times 逻辑：只补一次）
            if not r.get("ok") and args.try_pages and not args.page:
                for alt in PAGE_CANDIDATES:
                    if alt == page_url:
                        continue
                    print(f"  retry page={alt} ...")
                    r2 = harvest_once(
                        playwright=p,
                        cfg=cfg,
                        flow=args.flow,
                        proxy=proxy,
                        headless=not args.headed,
                        page_url=alt,
                        timeout_ms=int(args.timeout) * 1000,
                        use_local_sdk=bool(args.local_sdk),
                    )
                    if r2.get("ok"):
                        r = r2
                        break
                    r = r2

            results.append(r)
            print(
                f"  -> ok={r.get('ok')} has_sdk={r.get('has_sentinel_sdk')} "
                f"has_so={r.get('has_so')} so_len={r.get('so_len')} "
                f"t_len={r.get('t_len')} t_syntax={r.get('t_is_syntaxerror')} "
                f"err={r.get('error')}"
            )

    # 落盘：完整 results 含 token；另写摘要
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "flow": args.flow,
        "proxy": proxy or "direct",
        "headless": not args.headed,
        "times": len(results),
        "results": results,
        "summary": {
            "ok_n": sum(1 for r in results if r.get("ok")),
            "has_so_n": sum(1 for r in results if r.get("has_so")),
            "syntax_t_n": sum(1 for r in results if r.get("t_is_syntaxerror")),
        },
        "pass_criteria": {
            "has_so_stable": ">=1 and preferably all times",
            "t_not_syntaxerror": True,
            "so_header_len_near_jennifer": "~2900",
        },
        "note": (
            "P0 目标：真页稳定 has_so=true 且 t 非 SyntaxError。"
            "成功后再接协议 create opt-in；禁止伪造 so。"
        ),
    }
    json_path = out_dir / "harvest.json"
    # 写一份脱敏摘要（去掉完整 token 大字段，便于贴笔记）
    summary_results = []
    for r in results:
        s = {k: v for k, v in r.items() if k not in {"token_json", "so_header"}}
        if r.get("token_json"):
            s["token_keys"] = list((r.get("token_json") or {}).keys())
        summary_results.append(s)
    summary_payload = {**payload, "results": summary_results}

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "harvest_summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md = [
        "# Browser SO Harvest",
        "",
        f"- time: {payload['created_at']}",
        f"- flow: `{args.flow}`",
        f"- proxy: `{proxy or 'direct'}`",
        f"- headless: {not args.headed}",
        f"- ok: {payload['summary']['ok_n']}/{len(results)}",
        f"- has_so: {payload['summary']['has_so_n']}/{len(results)}",
        f"- t_syntaxerror: {payload['summary']['syntax_t_n']}/{len(results)}",
        "",
        "| # | ok | has_sdk | has_so | so_len | so_header_len | t_len | t_syntax | page | error |",
        "|---|----|---------|--------|--------|---------------|-------|----------|------|-------|",
    ]
    for i, r in enumerate(results, 1):
        md.append(
            "| {i} | {ok} | {sdk} | {so} | {slen} | {sh} | {tl} | {ts} | {page} | {err} |".format(
                i=i,
                ok=r.get("ok"),
                sdk=r.get("has_sentinel_sdk"),
                so=r.get("has_so"),
                slen=r.get("so_len"),
                sh=r.get("so_header_len"),
                tl=r.get("t_len"),
                ts=r.get("t_is_syntaxerror"),
                page=(r.get("final_url") or r.get("page_url") or "")[:48],
                err=str(r.get("error") or "")[:60].replace("|", "/"),
            )
        )
    md += [
        "",
        "## 判读",
        "",
        "1. has_so>=1 → P0 技术通路成立，可接协议 opt-in + 新根邮箱 P1。",
        "2. 全无 so 但 t 真 → 仍缺 sessionObserver/登录态行为；升级：登录后 about-you 再采。",
        "3. SDK 未暴露 → CSP/页不对；换 page 或 headed 人工确认。",
        "4. 禁止：假 so、关过滤、无 so 宣称存活已解。",
        "",
        f"full: `{json_path}`",
        "",
    ]
    (out_dir / "HARVEST.md").write_text("\n".join(md), encoding="utf-8")
    print("\n" + "\n".join(md))

    # exit: 有至少一次 has_so 视为研究通过；否则 1
    if payload["summary"]["has_so_n"] > 0:
        return 0
    if payload["summary"]["ok_n"] > 0:
        return 1  # token 出了但无 so
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

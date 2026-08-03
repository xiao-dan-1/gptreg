"""浏览器真页 Sentinel：token() + sessionObserverToken()（opt-in）。

默认注册路径仍是纯 Python PoW。仅 protocol.sentinel_source=browser 时使用。
- token JSON: {p,t,c,id,flow}（通常无 so 字段；so 不在 token() 返回值）
- so header:  {so,c,id,flow} 来自 sessionObserverToken → Nt(snapshot_dx)
- 禁止伪造 so；假值（SyntaxError / MDog…）丢弃
- OTP 阶段由 auth 强制 pow，本模块只服务 create 等 opt-in 调用

P1：browser 有 so 与 pow 无 so 在本环境 ≥2h 双活；真 so 非短窗必需。
见 capture/p1-so-survival-20260712/FINDINGS.md / SDK_SESSION_OBSERVER.md
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

FLOW_PAGE = {
    "authorize_continue": "https://auth.openai.com/email-verification",
    "oauth_create_account": "https://auth.openai.com/about-you",
    "username_password_create": "https://auth.openai.com/create-account/password",
}


def _is_fake_so(text: str) -> bool:
    s = text or ""
    return "SyntaxError" in s or s.startswith("MDogU3ludGF4")


def _sdk_url(cfg: dict[str, Any]) -> str:
    sv = str((cfg.get("protocol") or {}).get("sentinel_sv") or "20260219f9f6")
    return f"https://sentinel.openai.com/sentinel/{sv}/sdk.js"


def browser_proxy_from_cfg(cfg: dict[str, Any]) -> str | None:
    """浏览器出站：优先 chain_via（7890），再 default。"""
    protocol = cfg.get("protocol") or {}
    override = (protocol.get("sentinel_browser_proxy") or "").strip()
    if override.lower() in {"empty", "none", "direct"}:
        return None
    if override:
        return override
    dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    chain = (dyn.get("chain_via") or "").strip()
    if chain:
        return chain
    default = ((cfg.get("proxy") or {}).get("default") or "").strip()
    return default or None


def _normalize_so_header(
    so_raw: str | None,
    *,
    token_text: str,
    device_id: str,
    flow: str,
) -> tuple[str | None, dict[str, Any]]:
    """sessionObserverToken 输出 → openai-sentinel-so-token JSON。"""
    meta: dict[str, Any] = {"has_so": False, "so_len": 0, "so_is_fake": False}
    if not so_raw or not str(so_raw).strip():
        return None, meta
    raw = str(so_raw)
    if _is_fake_so(raw):
        meta["so_is_fake"] = True
        return None, meta

    so_parsed: Any = None
    try:
        so_parsed = json.loads(raw)
    except Exception:
        so_parsed = None

    so_header: str | None = None
    if isinstance(so_parsed, dict) and so_parsed.get("so"):
        if _is_fake_so(str(so_parsed.get("so") or "")):
            meta["so_is_fake"] = True
            return None, meta
        wrapper = {
            "so": so_parsed["so"],
            "c": so_parsed.get("c") or "",
            "id": so_parsed.get("id") or device_id,
            "flow": so_parsed.get("flow") or flow,
        }
        if not wrapper["c"]:
            try:
                wrapper["c"] = str((json.loads(token_text) or {}).get("c") or "")
            except Exception:
                pass
        so_header = json.dumps(wrapper, separators=(",", ":"), ensure_ascii=False)
        meta["has_so"] = True
        meta["so_len"] = len(str(wrapper["so"]))
    else:
        so_val = so_parsed if isinstance(so_parsed, str) else raw
        if _is_fake_so(str(so_val)):
            meta["so_is_fake"] = True
            return None, meta
        c_val = ""
        try:
            c_val = str((json.loads(token_text) or {}).get("c") or "")
        except Exception:
            pass
        so_header = json.dumps(
            {"so": so_val, "c": c_val, "id": device_id, "flow": flow},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        meta["has_so"] = True
        meta["so_len"] = len(str(so_val))

    if so_header and _is_fake_so(so_header):
        meta.update({"has_so": False, "so_is_fake": True, "so_len": 0})
        return None, meta
    meta["so_header_len"] = len(so_header or "")
    return so_header, meta


def harvest_browser_sentinel(
    cfg: dict[str, Any],
    *,
    flow: str,
    device_id: str,
    proxy: str | None = None,
    headless: bool | None = None,
    page_url: str | None = None,
    timeout_s: int | None = None,
    use_local_sdk: bool | None = None,
) -> dict[str, Any]:
    """真 Chrome 采 token + so。device_id 应与协议 session.oai-did 一致。"""
    from playwright.sync_api import TimeoutError as PwTimeout
    from playwright.sync_api import sync_playwright

    protocol = cfg.get("protocol") or {}
    browser_cfg = cfg.get("browser") or {}
    if headless is None:
        headless = bool(protocol.get("sentinel_browser_headless", True))
    if timeout_s is None:
        timeout_s = int(protocol.get("sentinel_browser_timeout") or 60)
    if use_local_sdk is None:
        use_local_sdk = bool(protocol.get("sentinel_browser_local_sdk", False))
    page_url = (
        page_url
        or protocol.get("sentinel_browser_page")
        or FLOW_PAGE.get(flow)
        or "https://auth.openai.com/about-you"
    )
    if proxy is None:
        proxy = browser_proxy_from_cfg(cfg)

    timeout_ms = max(10, int(timeout_s)) * 1000
    t0 = time.time()
    out: dict[str, Any] = {
        "ok": False,
        "mode": "browser",
        "flow": flow,
        "device_id": device_id,
        "page_url": page_url,
        "proxy": proxy or "direct",
        "headless": headless,
        "token": None,
        "so_header": None,
        "has_so": False,
        "so_len": 0,
        "error": None,
    }

    launch_kwargs: dict[str, Any] = {
        "channel": "chrome",
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            f"--lang={browser_cfg.get('language') or 'en-US'}",
        ],
    }
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            context = browser.new_context(
                user_agent=browser_cfg.get("user_agent") or None,
                locale=browser_cfg.get("language") or "en-US",
                viewport={
                    "width": int(browser_cfg.get("screen_width") or 1920),
                    "height": int(browser_cfg.get("screen_height") or 1080),
                },
            )
            for domain in (".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"):
                try:
                    context.add_cookies(
                        [
                            {
                                "name": "oai-did",
                                "value": device_id,
                                "domain": domain,
                                "path": "/",
                            }
                        ]
                    )
                except Exception:
                    pass

            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception as exc:
                out["nav_error"] = f"{type(exc).__name__}: {exc}"
            out["final_url"] = page.url

            try:
                page.mouse.move(120, 160)
                page.mouse.move(420, 280, steps=8)
                page.mouse.wheel(0, 200)
                page.wait_for_timeout(400)
            except Exception:
                pass

            from pathlib import Path

            root = Path(cfg.get("_root") or Path(__file__).resolve().parent.parent)
            sdk_local = root / "vendor" / "sentinel" / "sdk.js"
            try:
                if use_local_sdk and sdk_local.exists():
                    page.add_script_tag(path=str(sdk_local))
                    out["sdk_load_mode"] = "local_file"
                else:
                    page.add_script_tag(url=_sdk_url(cfg))
                    out["sdk_load_mode"] = "remote_url"
                page.wait_for_timeout(500)
            except Exception as exc:
                out["error"] = f"sdk_load: {type(exc).__name__}: {exc}"
                out["elapsed_s"] = round(time.time() - t0, 3)
                return out

            has_sdk = page.evaluate(
                "() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')"
            )
            if not has_sdk:
                try:
                    page.add_script_tag(url=_sdk_url(cfg))
                    page.wait_for_timeout(800)
                    has_sdk = page.evaluate(
                        "() => !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')"
                    )
                    out["sdk_load_mode"] = "remote_url_retry"
                except Exception as exc:
                    out["sdk_retry_error"] = f"{type(exc).__name__}: {exc}"
            out["has_sentinel_sdk"] = bool(has_sdk)
            if not has_sdk:
                out["error"] = "SentinelSDK.token 未暴露"
                out["elapsed_s"] = round(time.time() - t0, 3)
                return out

            try:
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
                for i in range(3):
                    page.mouse.move(100 + i * 80, 150 + i * 40, steps=5)
                    page.wait_for_timeout(350)
                page.mouse.wheel(0, 300)
                page.wait_for_timeout(400)

                bundle = page.evaluate(
                    """async (flow) => {
                        const out = {
                          sdk_keys: window.SentinelSDK ? Object.keys(window.SentinelSDK) : [],
                          token: null, so: null, token_err: null, so_err: null,
                        };
                        try {
                          const t = await window.SentinelSDK.token(flow);
                          out.token = (typeof t === 'string') ? t : JSON.stringify(t);
                        } catch (e) { out.token_err = String(e && e.stack || e); }
                        try {
                          for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 400));
                          if (typeof window.SentinelSDK.sessionObserverToken === 'function') {
                            const s = await window.SentinelSDK.sessionObserverToken(flow);
                            if (s != null) out.so = (typeof s === 'string') ? s : JSON.stringify(s);
                          } else { out.so_err = 'no sessionObserverToken API'; }
                        } catch (e) { out.so_err = String(e && e.stack || e); }
                        return out;
                    }""",
                    flow,
                )
            except PwTimeout as exc:
                out["error"] = f"token_timeout: {exc}"
                out["elapsed_s"] = round(time.time() - t0, 3)
                return out
            except Exception as exc:
                out["error"] = f"token_eval: {type(exc).__name__}: {exc}"
                out["elapsed_s"] = round(time.time() - t0, 3)
                return out

            token_text = (bundle or {}).get("token") or ""
            so_raw = (bundle or {}).get("so")
            out["sdk_keys"] = (bundle or {}).get("sdk_keys")
            out["token_err"] = (bundle or {}).get("token_err")
            out["so_api_err"] = (bundle or {}).get("so_err")
            if not token_text:
                out["error"] = out.get("token_err") or "empty token"
                out["elapsed_s"] = round(time.time() - t0, 3)
                return out

            # 丢弃 token JSON 内假 so 字段（若有）
            try:
                parsed = json.loads(token_text)
                so_val = parsed.get("so")
                if isinstance(so_val, str) and _is_fake_so(so_val):
                    parsed.pop("so", None)
                    token_text = json.dumps(parsed, separators=(",", ":"))
            except Exception:
                pass

            so_header, so_meta = _normalize_so_header(
                so_raw if isinstance(so_raw, str) else None,
                token_text=token_text,
                device_id=device_id,
                flow=flow,
            )
            out["token"] = token_text
            out["so_header"] = so_header
            out["has_so"] = bool(so_meta.get("has_so") and so_header)
            out["so_len"] = int(so_meta.get("so_len") or 0)
            out["so_header_len"] = len(so_header or "")
            out["so_is_fake"] = bool(so_meta.get("so_is_fake"))
            out["ok"] = True
            out["elapsed_s"] = round(time.time() - t0, 3)
            try:
                tj = json.loads(token_text)
                out["t_len"] = len(str(tj.get("t") or ""))
                out["token_keys"] = list(tj.keys())
            except Exception:
                pass
            return out
        finally:
            try:
                browser.close()
            except Exception:
                pass


def harvest_for_session(session: Any, flow: str) -> tuple[str, str | None, dict[str, Any]]:
    """给 BrowserSession 用：返回 (token, so_header, meta)。"""
    cfg = session.cfg
    result = harvest_browser_sentinel(
        cfg,
        flow=flow,
        device_id=session.device_id,
        proxy=browser_proxy_from_cfg(cfg),
    )
    if not result.get("ok") or not result.get("token"):
        raise RuntimeError(result.get("error") or "browser sentinel failed")
    token = str(result["token"])
    so = result.get("so_header")
    if so and _is_fake_so(str(so)):
        so = None
        result["has_so"] = False
        result["so_is_fake"] = True
    return token, so if so else None, result

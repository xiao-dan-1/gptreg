"""Sentinel 引擎注册表。

每个引擎独立实现 generate(session, flow, cfg) -> EngineResult(token, so, meta)。
auth.make_sentinel_headers 只按 source 查注册表调用,新增引擎不改 auth(开闭原则)。

引擎一览:
  pow                  纯 Python FNV-1a(默认,OTP 用,通常无 so)
  browser              真 Chrome token() + sessionObserverToken()
  quickjs              Node VM 跑 sdk.js 产真 t + vm so
  quickjs_pwd_v3       密码模式:quickjs 产 username_password_create t(register 无 so)
  browser_t_quickjs_so 真 t + vm so(控制变量实验)
  quickjs_t_browser_so vm t + 真 so(混合模式,存活实证)
  node                 Node VM 跑 sdk.js 产 t(假 t 非空即过 create)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EngineResult:
    token: str
    so: str | None
    meta: dict[str, Any] = field(default_factory=dict)


class SentinelEngine:
    name: str = ""

    def generate(self, session, flow: str, cfg: dict) -> EngineResult:
        raise NotImplementedError


def _t_len_of(token: str) -> int:
    try:
        return len(str(json.loads(token).get("t") or ""))
    except Exception:
        return 0


def _strip_fake_so_token(token: str) -> str:
    """从 token JSON 里剥掉假 so 字段(SyntaxError / MDogU3ludGF4)。"""
    try:
        parsed = json.loads(token)
        so_val = parsed.get("so")
        if isinstance(so_val, str) and (
            "SyntaxError" in so_val or so_val.startswith("MDogU3ludGF4")
        ):
            parsed.pop("so", None)
            return json.dumps(parsed, separators=(",", ":"))
    except Exception:
        pass
    return token


def _request_sentinel(session, flow: str) -> dict:
    """sentinel/req 拿 challenge(不依赖 auth,避免循环依赖)。"""
    from gptreg.sentinel import (
        build_sentinel_request_body,
        generate_requirements_token,
        log_chatreq_obs,
    )

    p = generate_requirements_token(session.cfg, session.device_id)
    body = build_sentinel_request_body(p, session.device_id, flow)
    url = "https://sentinel.openai.com/backend-api/sentinel/req"
    logger.info("[Sentinel] req flow=%s", flow)
    resp = session.post(url, headers=session.sentinel_headers(), data=body)
    resp.raise_for_status()
    data = resp.json()
    session._last_chatreq_obs = log_chatreq_obs(  # type: ignore[attr-defined]
        data, flow=flow, http=getattr(resp, "status_code", None)
    )
    return data


class BrowserEngine(SentinelEngine):
    """真 Chrome token() + sessionObserverToken()(真 so)。"""
    name = "browser"

    def generate(self, session, flow, cfg):
        from gptreg.browser_sentinel import harvest_for_session

        token, so, meta = harvest_for_session(session, flow)
        if so and ("SyntaxError" in so or "MDogU3ludGF4" in so):
            logger.warning("[Sentinel] browser so-header 假值，丢弃 flow=%s", flow)
            so = None
        return EngineResult(token, so, {
            "sdk_keys": meta.get("sdk_keys"),
            "elapsed_s": meta.get("elapsed_s"),
            "so_api_err": meta.get("so_api_err"),
        })


class QuickjsEngine(SentinelEngine):
    """Node VM 跑 sdk.js 产真 t + vm so。"""
    name = "quickjs"

    def generate(self, session, flow, cfg):
        from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs

        token, so = get_sentinel_token_via_quickjs(
            session, session.device_id, flow=flow, cfg=cfg,
        )
        return EngineResult(token, so, {"t_len": _t_len_of(token)})


class PwdEngine(SentinelEngine):
    """密码模式注册:quickjs 产 username_password_create t,register 不需要 so。"""
    name = "quickjs_pwd_v3"

    def generate(self, session, flow, cfg):
        from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs

        token, _so = get_sentinel_token_via_quickjs(
            session, session.device_id, flow=flow, cfg=cfg,
        )
        # 密码 register flow 无 so 字段,不产 so
        return EngineResult(token, None, {"t_len": _t_len_of(token)})


class HybridEngine(SentinelEngine):
    """拆分 t/so 来源。子类设 t_from / so_from。"""
    t_from = "quickjs"
    so_from = "browser"

    def generate(self, session, flow, cfg):
        from gptreg.browser_sentinel import harvest_for_session
        from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs

        token_b, so_b, _meta = harvest_for_session(session, flow)
        token_v, so_v = get_sentinel_token_via_quickjs(
            session, session.device_id, flow=flow, cfg=cfg,
        )
        token = token_b if self.t_from == "browser" else token_v
        so = so_b if self.so_from == "browser" else so_v
        return EngineResult(token, so, {
            "t_source": self.t_from,
            "so_source": self.so_from,
            "t_len": _t_len_of(token),
        })


class BrowserTQuickjsSoEngine(HybridEngine):
    """真 t + vm(假) so。"""
    name = "browser_t_quickjs_so"
    t_from = "browser"
    so_from = "quickjs"


class QuickjsTBrowserSoEngine(HybridEngine):
    """vm t + 真 so(存活实证的混合模式)。"""
    name = "quickjs_t_browser_so"
    t_from = "quickjs"
    so_from = "browser"


class NodeEngine(SentinelEngine):
    """Node VM 跑 sdk.js 产 t(假 t 非空即过 create)。"""
    name = "node"

    def generate(self, session, flow, cfg):
        from gptreg.sentinel import build_so_header, generate_sentinel_token_via_node

        challenge = _request_sentinel(session, flow)
        token = generate_sentinel_token_via_node(
            cfg, challenge, flow, session.device_id,
            user_agent=session.user_agent,
        )
        token = _strip_fake_so_token(token)
        so = build_so_header(token, session.device_id, flow)
        return EngineResult(token, so, {"t_len": _t_len_of(token)})


class PowEngine(SentinelEngine):
    """纯 Python FNV-1a PoW(OTP 默认)。"""
    name = "pow"

    def generate(self, session, flow, cfg):
        from gptreg.sentinel import SentinelPoW, resolve_pow_so_header

        browser_cfg = cfg.get("browser") or {}
        pow_engine = SentinelPoW(
            ua=session.user_agent,
            sv=getattr(session, "sentinel_sv", "") or "",
            device_id=session.device_id,
            cores=browser_cfg.get("hardware_concurrency"),
            screen_w=int(browser_cfg.get("screen_width") or 1920),
            screen_h=int(browser_cfg.get("screen_height") or 1080),
        )
        token = pow_engine.build(session.session, session.device_id, flow)
        chatreq_obs = getattr(pow_engine, "last_chatreq_obs", None) or {}

        # 丢假 so + 写 oai-sc cookie
        token = _strip_fake_so_token(token)
        try:
            parsed = json.loads(token)
            c_tok = str(parsed.get("c") or "").strip()
            if c_tok and hasattr(session, "set_oai_sc"):
                session.set_oai_sc(c_tok)
        except Exception:
            pass

        proto = cfg.get("protocol") or {}
        pow_so_source = str(proto.get("pow_so_source") or "none").strip().lower()
        so = resolve_pow_so_header(token, session.device_id, flow, pow_so_source=pow_so_source)
        if so and ("SyntaxError" in so or "MDogU3ludGF4" in so):
            logger.warning("[Sentinel] so-header 含 SyntaxError，丢弃")
            so = None
        return EngineResult(token, so, {"pow_so_source": pow_so_source, "chatreq": chatreq_obs})


_ENGINES: dict[str, SentinelEngine] = {}
for _engine in (
    BrowserEngine(),
    QuickjsEngine(),
    PwdEngine(),
    BrowserTQuickjsSoEngine(),
    QuickjsTBrowserSoEngine(),
    NodeEngine(),
    PowEngine(),
):
    _ENGINES[_engine.name] = _engine


def get_engine(source: str) -> SentinelEngine:
    """按 source 名取引擎;未知 source 回退 pow(兼容旧配置)。"""
    return _ENGINES.get(source or "pow", _ENGINES["pow"])


def engine_names() -> list[str]:
    return sorted(_ENGINES)

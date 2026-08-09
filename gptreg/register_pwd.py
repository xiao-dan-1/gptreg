"""密码注册 + TOTP 2FA 核心（结构化结果，供 CLI/批量复用）。

主路线整条链：signin→authorize→register(设密码)→OTP 收码→validate→
create_account(quickjs 真 t + browser 真 so 并行)→session→mfa/enroll→
activate_enrollment(2FA 真激活)→构造落盘 record。

返回结构化 RegistrationResult(outcome/diag/record)，取代脚本里 print 判定：
CLI 与 batch_totp 共享本模块，失败类型决定主号"可重试 vs 永久弃用"。
"""
from __future__ import annotations

import json
import logging
import random
import re
import string
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gptreg import auth
from gptreg.browser_sentinel import harvest_browser_sentinel
from gptreg.health import check_account_health
from gptreg.mail.mail_util import MailClientError
from gptreg.mail.pool import parse_mail_line  # noqa: F401  (CLI 选号复用)
from gptreg.mail.wait_otp import wait_otp_with_retry
from gptreg.proxyutil import build_dynamic_proxy, resolve_proxy
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs
from gptreg.session import BrowserSession
from gptreg.account_store import save_account

logger = logging.getLogger(__name__)

FLOW_PWD = "username_password_create"
FLOW_OAUTH = "oauth_create_account"
REGISTER_URL = "https://auth.openai.com/api/accounts/user/register"
CREATE_URL = "https://auth.openai.com/api/accounts/create_account"
PASSWORD_REFERER = "https://auth.openai.com/create-account/password"
ABOUT_YOU_REFERER = "https://auth.openai.com/about-you"
ENROLL_URL = "https://chatgpt.com/backend-api/accounts/mfa/enroll"
ACTIVATE_URL = "https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment"
MFA_INFO_URL = "https://chatgpt.com/backend-api/accounts/mfa_info"


class RegisterOutcome(Enum):
    """失败类型：决定主号生命周期（可重试 vs 永久弃用）。"""

    SUCCESS = "success"
    IP_BLOCKED = "ip_blocked"  # register 400, 出口 IP 信誉不足 → 换 IP 可重试
    MAIL_REGISTERED = "mail_registered"  # 落 log-in, 邮箱已在 OpenAI 注册 → 永久弃用
    SO_FAILED = "so_failed"  # so 采集失败(重试后), 无 so 必死 → 中止
    OTP_FAILED = "otp_failed"  # 收码超时/失败
    CREATE_FAILED = "create_failed"  # create 拒建/失败
    SESSION_FAILED = "session_failed"  # callback/session 失败
    HEALTH_FAILED = "health_failed"  # create 后健康检查失败(秒封/吊销)
    ENROLL_FAILED = "enroll_failed"  # 2FA enroll 失败(账号已建, 落盘待补)


@dataclass
class RegistrationResult:
    outcome: RegisterOutcome
    email: str
    diag: dict[str, Any] = field(default_factory=dict)
    record: dict[str, Any] | None = None


class _RegisterBlocked(RuntimeError):
    """register 400 + 落点非 log-in：IP 信誉不足, 换 sid 可重试。"""


class _MailRegistered(RuntimeError):
    """register 400 + 落 log-in：邮箱已注册, 永久弃用。"""


class _SoFailed(RuntimeError):
    pass


class _OtpFailed(RuntimeError):
    pass


class _CreateFailed(RuntimeError):
    pass


class _SessionFailed(RuntimeError):
    pass


class _EnrollFailed(RuntimeError):
    pass


def _landing_diag(final: str) -> str:
    """authorize 落点 → 诊断文本(register 400 时输出, 定位根因)。"""
    land = final or "?"
    if "email-verification" in land:
        return "email-verification → 需先验证邮箱(新流程) 或主号已注册; 换 IP/用别名再试"
    if "log-in" in land or "/login" in land:
        return "log-in → 邮箱已注册(登录流程), register 不合法 → 永久弃用该邮箱"
    if "create-account" in land:
        return "create-account/password → 未注册, 仍 400 多为出口 IP 信誉"
    return land[:60]


def timing_str(diag: dict[str, Any]) -> str:
    """按 diag 字段拼耗时归因(段增量口径, 缺失段跳过)。

    register_pwd 各 stage 记录的 signin_s/register_s/otp_s/create_s/session_s 均为
    段增量(round(time.time()-段起点,1)), 与 health_s/enroll_s 同口径——成功/失败通用:
    失败发生在哪段, 就只有该段及之前的字段。
    verify_pwd_totp / batch_totp 共用本函数, 避免双份口径漂移。
    """
    d = diag or {}
    parts: list[str] = []
    if d.get("setup_s") is not None:
        parts.append(f"setup={d['setup_s']}s")
    sn = d.get("signin_s")
    rg = d.get("register_s")
    otp = d.get("otp_s")
    cr = d.get("create_s")
    ss = d.get("session_s")
    if any(x is not None for x in (sn, rg, otp, cr, ss)):
        ch = d.get("otp_channel") or "?"
        delay = d.get("otp_delay_s")
        delay_str = f"到件{delay:.1f}s" if delay is not None else "到件?"
        seg = f"signin={sn:.1f}s" if sn is not None else "signin=?"
        seg += f" register={rg:.1f}s" if rg is not None else " register=?"
        if otp is not None:
            seg += f" OTP段({ch})={otp:.1f}s[{delay_str}]"
        seg += f" create段={cr:.1f}s" if cr is not None else " create段=?"
        seg += f" session={ss:.1f}s" if ss is not None else " session=?"
        parts.append(seg)
    # create 内部: 并行(t+so 采集) + create HTTP(建号请求)
    cp = d.get("create_parallel")
    if cp is not None:
        st_ = d.get("so_timing") or {}
        # nav/sdk/token 是采集开始后的累计时刻(非段增量); token 含 SDK init+交互,
        # 故 token 时刻 > nav/sdk(三者相加会 > 总耗时, 勿误读为串行)
        so_inner = ""
        if st_:
            so_inner = f"[nav={st_.get('nav')}s sdk={st_.get('sdk')}s token={st_.get('token')}s(累计时刻)]"
        so_att = d.get("so_attempts")
        so_att_str = f" retry={so_att - 1}" if so_att and so_att > 1 else ""
        t_s = d.get("t_s")
        so_s = d.get("so_s")
        parts.append(
            f"并行(t={t_s:.1f}s so={so_s:.1f}s{so_inner}{so_att_str})={cp:.1f}s"
            if t_s is not None and so_s is not None
            else f"并行(t={t_s} so={so_s})={cp:.1f}s"
        )
    if d.get("create_http_s") is not None:
        parts.append(f"create http={d['create_http_s']:.1f}s")
    if d.get("health_s") is not None:
        parts.append(f"health={d['health_s']}s")
    if d.get("enroll_s") is not None:
        parts.append(f"enroll={d['enroll_s']}s")
    return " ".join(parts)


def _stage_signin(session: BrowserSession, email: str, diag: dict[str, Any]) -> str:
    """Stage 1: signin 序列(协议节奏内聚在 auth.signin_flow)。返回落点 URL。"""
    t0 = time.time()
    final = auth.signin_flow(session, email, follow_sleep=0.5, authorize_attempts=1)
    diag["landing"] = final
    diag["signin_s"] = round(time.time() - t0, 1)  # 纯 signin 段(signin 慢点定位)
    return final


def _stage_register(
    session: BrowserSession,
    cfg: dict[str, Any],
    email: str,
    password: str,
    final: str,
    st: dict[str, float],
    diag: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Stage 2: register(设密码) + send_otp。返回 (reg, send_url)。

    400 分类: 落 log-in=邮箱已注册(永久弃用) / 其他=IP 信誉(换 sid 可重试)。
    """
    _t0 = time.time()
    # 静默 quickjs 默认 log(其 so_len 是 vm so, 密码 register 无 so), 自行明确打印
    token, _ = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_PWD, cfg=cfg,
                                              log=lambda m: None)
    logger.info("  [quickjs/t] register 真 t 就绪 t_len=%s (so: 密码 register 无 so)", len(token))
    headers = session.auth_api_headers(referer=PASSWORD_REFERER)
    headers["openai-sentinel-token"] = token
    resp = session.post(REGISTER_URL, headers=headers,
                        data=json.dumps({"username": email, "password": password}))
    # invalid_auth_step 修复: 干净号(落 email-verification)遇 session 未推进时
    # warm_about_you 推进后重试一次(对齐 create_account 做法)——否则干净号被误判 IP_BLOCKED 弃掉
    if resp.status_code == 400 and "invalid_auth_step" in (resp.text or "") \
            and "log-in" not in (final or "") and "/login" not in (final or ""):
        logger.warning("[Auth] register invalid_auth_step(干净号 session 未推进), warm_about_you 重试")
        auth.warm_about_you(session)
        time.sleep(0.3)
        resp = session.post(REGISTER_URL, headers=headers,
                            data=json.dumps({"username": email, "password": password}))

    if resp.status_code != 200:
        err = f"register HTTP {resp.status_code}: {resp.text[:150]}"
        # 提取服务器原始 code(如 invalid_auth_step), 让"已注册"判定可验证
        srv_code = ""
        srv_redirect = ""
        try:
            _ej = resp.json()
            _err = _ej.get("error") or {}
            srv_code = str(_err.get("code") or "")
            srv_redirect = str(_err.get("redirect_uri") or "")
        except Exception:
            pass
        # 落 log-in = 邮箱已注册(登录流程) → 永久弃用;
        # 服务端 invalid_auth_step + redirect login_with 佐证(已注册邮箱不可再注册)
        if "log-in" in (final or "") or "/login" in (final or ""):
            e = _MailRegistered(err)
            e.diag = {
                "landing_diag": _landing_diag(final),
                "reason": err,
                "srv_code": srv_code,
                "srv_redirect": srv_redirect,
            }
            raise e
        e = _RegisterBlocked(err)
        e.diag = {
            "landing_diag": _landing_diag(final),
            "reason": err,
            "srv_code": srv_code,
            "srv_redirect": srv_redirect,
        }
        raise e
    reg = resp.json()
    diag["register_s"] = round(time.time() - _t0, 1)  # 纯 register 段(设密码+quickjs t)

    send_url = reg.get("continue_url") or "https://auth.openai.com/api/accounts/email-otp/send"
    r = session.get(send_url, headers=session.auth_navigate_headers(referer=PASSWORD_REFERER),
                    allow_redirects=True)
    diag["send_otp"] = r.status_code
    return reg, send_url


def _stage_wait_otp(
    session: BrowserSession,
    cfg: dict[str, Any],
    account: dict[str, Any],
    email: str,
    send_url: str,
    proxy_url: str,
    st: dict[str, float],
    diag: dict[str, Any],
) -> str:
    """Stage 3: 收码(共享 wait_otp_with_retry: 代理决策+重发+到件延迟)。"""
    _t0 = time.time()
    mail_cfg = cfg.get("mail", {})
    otp_timeout = int(mail_cfg.get("otp_wait", 150) or 150)
    otp_max_attempts = max(1, int(mail_cfg.get("otp_max_attempts", 2) or 2))
    otp, extra = wait_otp_with_retry(
        cfg, account, email=email,
        after_ts=st["start"], proxy_url=proxy_url,
        send_url=send_url, session=session,
        max_attempts=otp_max_attempts, timeout=otp_timeout,
        interval=3, settle_seconds=5,
    )
    diag["otp"] = otp
    diag["otp_s"] = round(time.time() - _t0, 1)  # 纯 OTP 段(收码+重发)
    diag["otp_got"] = True  # 邮箱已推进到 OTP 验证(后续不可重试同邮箱)
    for k, v in extra.items():
        diag[k] = v

    # validate
    auth.validate_email_otp(session, otp, None)
    return otp


def _stage_create(
    session: BrowserSession,
    cfg: dict[str, Any],
    name: str,
    bday: str,
    proxy_url: str,
    st: dict[str, float],
    diag: dict[str, Any],
) -> tuple[str, str, str]:
    """Stage 4: create_account —— quickjs 真 t 与 browser 真 so 并行(独立资源)。

    so 采集失败重试 3 次, 仍无 so 中止(无 so 账号必死, 测活实证)。
    返回 (tok2, so_b, continue_url)。
    """
    _t0 = time.time()
    holder: dict[str, Any] = {}

    def _gen_t() -> None:
        _ct = time.time()
        try:
            # 静默 quickjs 默认 log(so_len 是 vm so, 会被忽略); so 由 browser 采集
            tok, _ = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_OAUTH, cfg=cfg,
                                                    log=lambda m: None)
            holder["tok2"] = tok
            logger.info("  [quickjs/t] create 真 t 就绪 t_len=%s (%.1fs, so 由 browser 采集)", len(tok), time.time() - _ct)
        except Exception as exc:
            holder["t_err"] = f"{type(exc).__name__}: {exc}"
        holder["t_s"] = time.time() - _ct

    def _gen_so() -> None:
        _ct = time.time()
        so = None
        # 无 so 必死(测活实证), 采集失败重试 3 次, 仍失败主线程中止
        so_attempts = 0
        for _try in range(3):
            so_attempts = _try + 1
            try:
                br = harvest_browser_sentinel(cfg, flow=FLOW_OAUTH, device_id=session.device_id,
                                              proxy=proxy_url, headless=True, timeout_s=90)
                if br.get("ok") and br.get("so_header"):
                    so = br["so_header"]
                    # so 内部细分(nav/SDK加载/token采集), 定位慢点
                    holder["so_timing"] = {
                        "nav": br.get("nav_s"), "sdk": br.get("sdk_s"),
                        "token": br.get("token_s"), "total": br.get("elapsed_s"),
                    }
                    break
            except Exception as exc:
                holder["so_warn"] = f"{type(exc).__name__}: {str(exc)[:80]}"
            time.sleep(1)
        holder["so_attempts"] = so_attempts  # 实际尝试次数(>1 说明重试过, so 稳定性)
        holder["so_b"] = so
        holder["so_s"] = time.time() - _ct

    _ct0 = time.time()
    _th_t = threading.Thread(target=_gen_t)
    _th_so = threading.Thread(target=_gen_so)
    _th_t.start()
    _th_so.start()
    _th_t.join()
    _th_so.join()
    tok2 = str(holder.get("tok2") or "")
    so_b = holder.get("so_b")
    diag["t_s"] = round(float(holder.get("t_s", 0)), 1)
    diag["so_s"] = round(float(holder.get("so_s", 0)), 1)
    if holder.get("so_attempts") is not None:
        diag["so_attempts"] = int(holder["so_attempts"])  # so 采集尝试次数(重试标注)
    diag["create_parallel"] = round(time.time() - _ct0, 1)
    if holder.get("so_timing"):
        diag["so_timing"] = holder["so_timing"]
    if holder.get("t_err"):
        t_err = str(holder["t_err"])
        from gptreg.auth import _is_transient
        # 归因精确: 网络瞬时错误(隧道断流, 如 SSLError) vs 本地 quickjs 问题
        kind = "网络瞬时错误(隧道断流)" if _is_transient(Exception(t_err)) else "本地 quickjs 问题"
        raise _SessionFailed(f"quickjs t 生成失败[{kind}]: {t_err[:120]}")
    if not so_b:
        warn = holder.get("so_warn") or ""
        raise _SoFailed(f"browser so 采集失败(重试3次后仍无 so): {str(warn)[:120]}")

    _http_t0 = time.time()
    h2 = session.auth_api_headers(referer=ABOUT_YOU_REFERER)
    h2["openai-sentinel-token"] = tok2
    h2["openai-sentinel-so-token"] = so_b
    resp2 = session.post(CREATE_URL, headers=h2, data=json.dumps({"name": name, "birthdate": bday}))
    diag["create_http"] = resp2.status_code
    diag["create_http_s"] = round(time.time() - _http_t0, 1)  # create HTTP 请求本身耗时
    diag["create_s"] = round(time.time() - _t0, 1)  # 纯 create 段(t+so 并行 + create HTTP)
    if resp2.status_code != 200:
        raise _CreateFailed(f"create_account HTTP {resp2.status_code}: {resp2.text[:150]}")
    cr = resp2.json()
    cu = cr.get("continue_url") or cr.get("url")
    if not cu:
        raise _CreateFailed("create_account 无 continue_url")
    return tok2, so_b, cu


def _stage_session(
    session: BrowserSession,
    cu: str,
    st: dict[str, float],
    diag: dict[str, Any],
) -> tuple[str, str, str, list[dict[str, Any]]]:
    """Stage 5: callback → session(access_token)。返回 (at, session_token, refresh_token, cookies)。

    session_token(JWE, ~3月) 是 OpenAI 的刷新凭证——access_token 10 天过期后,
    靠它或 session_cookies 重抓 /api/auth/session 续期(研究实证, 见 refresh-research)。
    """
    _t0 = time.time()
    auth.follow_oauth_callback(session, cu)
    info = auth.fetch_session(session)
    at = info.get("accessToken")
    if not at:
        raise _SessionFailed("session 无 accessToken")
    diag["session_s"] = round(time.time() - _t0, 1)  # 纯 session 段(callback+session)
    cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                "secure": bool(getattr(c, "secure", False))}
               for c in session.session.cookies.jar]
    session_token = info.get("sessionToken") or ""
    refresh = info.get("refreshToken") or info.get("refresh_token") or ""
    return at, session_token, refresh, cookies


def _register_chain(
    cfg: dict[str, Any],
    account: dict[str, Any],
    email: str,
    password: str,
    name: str,
    bday: str,
    proxy_url: str,
) -> dict[str, Any]:
    """一次完整注册(signin→register→OTP→create→session), 返回注册凭据或抛分类异常。

    阶段序列编排器: 各阶段独立函数(Signin/Register/WaitOtp/Create/Session),
    统一 diag 累加, 任一段抛分类异常由 register_account 统一归类。
    """
    _setup_t0 = time.time()
    resolved = resolve_proxy(cfg, override=proxy_url)
    session = BrowserSession(cfg, proxy=resolved.session_url)
    st = {"start": time.time()}
    # setup_s: 隧道建立+会话初始化(在 st.start 之前, 故归入独立段, 不污染 signin 段)。
    # 隧道探活失败重建时偏大——IP 排查有价值(段外开销归因, 收窄"段和 vs 墙钟"差额)。
    diag: dict[str, Any] = {"setup_s": round(time.time() - _setup_t0, 1)}
    try:
        final = _stage_signin(session, email, diag)
        reg, send_url = _stage_register(session, cfg, email, password, final, st, diag)
        otp = _stage_wait_otp(session, cfg, account, email, send_url, resolved.session_url or None,
                              st, diag)
        tok2, so_b, cu = _stage_create(session, cfg, name, bday, resolved.session_url or None, st, diag)
        at, session_token, refresh_token, cookies = _stage_session(session, cu, st, diag)
        return {
            "at": at,
            "device_id": session.device_id,
            "cookies": cookies,
            "session_token": session_token,
            "refresh_token": refresh_token,
            "t_len": len(tok2),
            "so_len": len(so_b),
            "has_so": True,
            "proxy_used": resolved.upstream_url or resolved.session_url or "",
            "diag": diag,
        }, session, resolved
    except Exception as exc:
        # 附上 diag(含 otp_got 邮箱推进标记) 到异常, register_account 据此决定
        # 是否换 sid 重试同邮箱——邮箱已推进(OTP 收到)后不可重试同邮箱(会误判已注册)
        if not hasattr(exc, "diag"):
            try:
                exc.diag = dict(diag)  # type: ignore[attr-defined]
            except Exception:
                pass
        resolved.close()
        raise


def _enroll_totp(cfg: dict[str, Any], session: BrowserSession, reg: dict[str, Any]) -> dict[str, Any]:
    """用注册会话开 TOTP 2FA(enroll→activate), 复用注册隧道(不双重建 resolved)。"""
    try:
        # 注册 session 已含登录 cookies + oai-did, 直接复用——隧道贯穿整条链
        h6 = session.chatgpt_headers(referer="https://chatgpt.com/")
        h6["authorization"] = f"Bearer {reg['at']}"
        h6["oai-device-id"] = reg["device_id"]
        h6.pop("content-type", None)
        h6["content-type"] = "application/json"
        resp_enroll = session.post(ENROLL_URL, headers=h6, data=json.dumps({"factor_type": "totp"}), timeout=30)
        if resp_enroll.status_code != 200:
            raise _EnrollFailed(f"mfa/enroll HTTP {resp_enroll.status_code}: {resp_enroll.text[:200]}")
        ej = resp_enroll.json()
        enroll_secret = str(ej.get("secret") or "")
        session_id = ej.get("session_id")
        factor_id = (ej.get("factor") or {}).get("id")
        logger.info("  [enroll] mfa/enroll HTTP %s factor_id=%s", resp_enroll.status_code, factor_id)
        activated = False
        if enroll_secret and session_id and factor_id:
            import pyotp

            code6 = pyotp.TOTP(enroll_secret).now()
            resp_act = session.post(ACTIVATE_URL, headers=h6, data=json.dumps({
                "code": code6, "session_id": session_id,
                "factor_id": factor_id, "factor_type": "totp"}), timeout=30)
            activated = resp_act.status_code == 200 and '"success":true' in (resp_act.text or "")
            logger.info("  [enroll] activate_enrollment HTTP %s activated=%s", resp_act.status_code, activated)
            try:
                resp_info = session.get(MFA_INFO_URL, headers=h6, timeout=30)
                logger.info("  [enroll] mfa_info HTTP %s mfa_enabled=%s", resp_info.status_code,
                            '"mfa_enabled":true' in (resp_info.text or ""))
                if '"mfa_enabled":true' in (resp_info.text or ""):
                    activated = True
            except Exception as exc:
                logger.warning("  [enroll] mfa_info 查询失败: %s", str(exc)[:60])
        if not activated:
            raise _EnrollFailed("activate_enrollment 未确认 mfa_enabled=true")
        return {"totp_secret": enroll_secret, "totp_enrolled": True}
    finally:
        pass  # session/resolved 由 register_account 统一关闭


def _session_fail_retry(
    cfg: dict[str, Any], exc: Exception, email: str, proxy_url: str,
    att: int, last_diag: dict[str, Any], auto_retry: bool,
) -> tuple[RegistrationResult, str]:
    """SESSION_FAILED 处理: 瞬时网络错误(隧道中途断流/SSLError)换 sid 重试。

    根因: 7890(Clash) 偶发抖动导致已建隧道中途断流 → 非账号问题, 重试大概率成功。
    仅在邮箱未推进的早期段重试——OTP 已验证(邮箱进入 OpenAI 注册流程)后不可重试
    同邮箱, 否则重试会落 log-in 误判"已注册"(实测: quickjs t SSLError 在 OTP 后
    触发, 重试同邮箱 → 邮箱已注册误判)。
    返回 (result_or_None, new_proxy_url): result 非 None 表示终止, None 表示调用方应
    用 new_proxy_url 继续重试。
    """
    from gptreg.auth import _is_transient
    reason = f"{type(exc).__name__}: {str(exc)[:120]}"
    # 邮箱已推进(OTP 验证后): 不重试同邮箱, 直接失败(批量层换新邮箱)
    exc_diag = getattr(exc, "diag", None) or {}
    if exc_diag.get("otp_got"):
        last_diag["reason"] = reason
        last_diag["otp_got"] = True
        return RegistrationResult(RegisterOutcome.SESSION_FAILED, email, last_diag), proxy_url
    if not _is_transient(exc) or not auto_retry or att >= 2 or "-sid-" not in (proxy_url or "") or "-t-" not in (proxy_url or ""):
        last_diag["reason"] = reason
        return RegistrationResult(RegisterOutcome.SESSION_FAILED, email, last_diag), proxy_url
    # 瞬时网络错误(早期段, 邮箱未推进): 换 sid 重建隧道重试(基建抖动, 非账号问题)
    new_sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    new_url = re.sub(r"-sid-[^-]+-t-", f"-sid-{new_sid}-t-", proxy_url, count=1)
    last_diag["reason"] = reason
    last_diag["retry_sid"] = att + 1
    logger.warning("  [retry] 瞬时网络错误换 sid 重试 (%d/3): %s", att + 2, reason[:60])
    time.sleep(1)
    return None, new_url


def register_account(
    cfg: dict[str, Any],
    account: dict[str, Any],
    *,
    email: str,
    password: str,
    name: str,
    bday: str,
    proxy: str | None = None,
    auto_retry: bool = True,
) -> RegistrationResult:
    """执行一次密码注册 + TOTP 2FA, 返回结构化结果。

    proxy 空则走 config 动态模板(可换 sid 重试)。register 400(IP 信誉)自动换 sid
    重试最多 3 次; 落 log-in(邮箱已注册)直接 MAIL_REGISTERED 永久弃用。
    """
    t0 = time.time()
    proxy_url = proxy
    if not proxy_url:
        proxy_url = build_dynamic_proxy(cfg)
    reg: dict[str, Any] | None = None
    session: BrowserSession | None = None
    resolved = None
    last_diag: dict[str, Any] = {}
    last_outcome = RegisterOutcome.IP_BLOCKED

    for att in range(3):
        try:
            reg, session, resolved = _register_chain(cfg, account, email, password, name, bday, proxy_url)
            break
        except _MailRegistered as exc:
            return RegistrationResult(
                RegisterOutcome.MAIL_REGISTERED, email,
                getattr(exc, "diag", None) or {"reason": str(exc)[:150]},
            )
        except _RegisterBlocked as exc:
            last_outcome = RegisterOutcome.IP_BLOCKED
            last_diag = dict(getattr(exc, "diag", {}) or {})
            last_diag["attempt"] = att + 1
            logger.warning("  [warn] register 被拒(IP 风控): %s", str(exc)[:70])
            if not auto_retry or att >= 2 or "-sid-" not in (proxy_url or "") or "-t-" not in (proxy_url or ""):
                break
            new_sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            proxy_url = re.sub(r"-sid-[^-]+-t-", f"-sid-{new_sid}-t-", proxy_url, count=1)
            last_diag["retry_sid"] = att + 1
            logger.warning("  [retry] 换新 sid 重试 (%d/3)", att + 2)
            time.sleep(1)
        except _SoFailed as exc:
            return RegistrationResult(RegisterOutcome.SO_FAILED, email, {"reason": str(exc)[:150]})
        except (MailClientError, _OtpFailed) as exc:
            return RegistrationResult(RegisterOutcome.OTP_FAILED, email, {"reason": str(exc)[:150]})
        except _CreateFailed as exc:
            return RegistrationResult(RegisterOutcome.CREATE_FAILED, email, {"reason": str(exc)[:150]})
        except (_SessionFailed, Exception) as exc:
            # 瞬时网络错误(隧道中途断流/SSLError)换 sid 重建重试; 非瞬时终止
            res, new_url = _session_fail_retry(cfg, exc, email, proxy_url, att, last_diag, auto_retry)
            if res is not None:
                return res
            proxy_url = new_url

    if reg is None:
        last_diag["elapsed_s"] = round(time.time() - t0, 1)
        return RegistrationResult(last_outcome, email, last_diag)

    mail_main = account.get("email") or ""
    mail_type = str(account.get("mail_type") or "")
    # create 后即时健康检查(秒封检测) + 2FA 激活; 复用注册会话/隧道(贯穿整条链)
    try:
        _h_t0 = time.time()
        health = check_account_health(session, reg["at"])  # type: ignore[arg-type]
        _health_s = round(time.time() - _h_t0, 1)
        if health.get("status") != "ok":
            rec = _partial_record(reg, email, password, name, bday, mail_main, "health_failed",
                                  mail_type=mail_type)
            rec.setdefault("sentinel_obs", {})["health_s"] = _health_s
            save_account(cfg, record=rec)
            return RegistrationResult(RegisterOutcome.HEALTH_FAILED, email,
                                      {"reason": f"health {health.get('status')} http={health.get('http')}"}, rec)
        _en_t0 = time.time()
        totp = _enroll_totp(cfg, session, reg)  # type: ignore[arg-type]
        _enroll_s = round(time.time() - _en_t0, 1)
        record = _build_record(reg, email, password, name, bday, mail_main, totp,
                               health_s=_health_s, enroll_s=_enroll_s, mail_type=mail_type)
        save_account(cfg, record=record)
        diag = dict(reg.get("diag") or {})
        diag["health_s"] = _health_s
        diag["enroll_s"] = _enroll_s
        diag["elapsed_s"] = round(time.time() - t0, 1)
        return RegistrationResult(RegisterOutcome.SUCCESS, email, diag, record)
    except _EnrollFailed as exc:
        rec = _partial_record(reg, email, password, name, bday, mail_main, "registered_no_totp",
                              mail_type=mail_type)
        rec.setdefault("sentinel_obs", {})["health_s"] = _health_s
        save_account(cfg, record=rec)
        return RegistrationResult(RegisterOutcome.ENROLL_FAILED, email,
                                  {"reason": str(exc)[:150]}, rec)
    finally:
        if resolved is not None:
            resolved.close()


def _partial_record(reg, email, password, name, bday, mail_main, status, mail_type="") -> dict[str, Any]:
    return {
        "email": email, "password": password, "access_token": reg["at"],
        "session_token": reg.get("session_token", ""),  # 刷新凭证(~3月), token 过期续期用
        "refresh_token": reg["refresh_token"], "device_id": reg["device_id"],
        "name": name, "birthdate": bday, "mail_main": mail_main,
        "mail_type": mail_type,  # 号源类型(号源可靠性统计; 缺省靠域名推断兜底)
        "status": status, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_cookies": reg["cookies"], "proxy_used": reg.get("proxy_used", ""),
    }


def _build_record(reg, email, password, name, bday, mail_main, totp, health_s=None, enroll_s=None, mail_type="") -> dict[str, Any]:
    rec = _partial_record(reg, email, password, name, bday, mail_main, "ok", mail_type=mail_type)
    rec["totp_secret"] = totp["totp_secret"]
    rec["sentinel_obs"] = {
        "challenge_mode": "quickjs_pwd_v3",
        "create_has_so": reg["has_so"],
        "create_so_len": reg["so_len"],
        "t_len": reg["t_len"],
        "flow": FLOW_PWD,
        "create_flow": FLOW_OAUTH,
        "totp_enrolled": True,
        "health_s": health_s,   # 秒封检测耗时(段增量), 便于 2FA 激活/存活耗时分析
        "enroll_s": enroll_s,   # 2FA enroll→activate 耗时(段增量)
    }
    return rec

"""密码注册 + TOTP 2FA 核心（结构化结果，供 CLI/批量复用）。

主路线整条链：signin→authorize→register(设密码)→OTP 收码→validate→
create_account(quickjs 真 t + browser 真 so 并行)→session→mfa/enroll→
activate_enrollment(2FA 真激活)→构造落盘 record。

返回结构化 RegistrationResult(outcome/diag/record)，取代脚本里 print 判定：
CLI 与 batch_totp 共享本模块，失败类型决定主号"可重试 vs 永久弃用"。
"""
from __future__ import annotations

import json
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
from gptreg.config import _root, resolve_path
from gptreg.health import check_account_health
from gptreg.mail.pool import parse_mail_line  # noqa: F401  (CLI 选号复用)
from gptreg.mail.providers import UsedCodeCache, build_mail_client, mail_identity_key
from gptreg.proxyutil import build_dynamic_proxy, resolve_proxy
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs
from gptreg.session import BrowserSession
from gptreg.store import save_account

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


def _register_chain(
    cfg: dict[str, Any],
    account: dict[str, Any],
    email: str,
    password: str,
    name: str,
    bday: str,
    proxy_url: str,
) -> dict[str, Any]:
    """一次完整注册(signin→register→OTP→create→session), 返回注册凭据或抛分类异常。"""
    resolved = resolve_proxy(cfg, override=proxy_url)
    session = BrowserSession(cfg, proxy=resolved.session_url)
    st = {"start": time.time()}
    diag: dict[str, Any] = {}
    try:
        auth.get_providers(session)
        time.sleep(0.3)
        csrf = auth.get_csrf_token(session)
        time.sleep(0.3)
        au = auth.signin_openai(session, csrf, email)
        time.sleep(0.3)
        final = auth.follow_authorize(session, au, attempts=1)
        time.sleep(0.5)
        diag["landing"] = final

        # register(设密码) —— 400 分类: 落 log-in=邮箱已注册 / 其他=IP 信誉
        # 静默 quickjs 默认 log(其 so_len 是 vm so, 密码 register 无 so), 自行明确打印
        token, _ = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_PWD, cfg=cfg,
                                                  log=lambda m: None)
        print(f"  [quickjs/t] register 真 t 就绪 t_len={len(token)} (so: 密码 register 无 so)")
        headers = session.auth_api_headers(referer=PASSWORD_REFERER)
        headers["openai-sentinel-token"] = token
        resp = session.post(REGISTER_URL, headers=headers,
                            data=json.dumps({"username": email, "password": password}))
        if resp.status_code != 200:
            err = f"register HTTP {resp.status_code}: {resp.text[:150]}"
            if "log-in" in (final or "") or "/login" in (final or ""):
                e = _MailRegistered(err)
                e.diag = {"landing_diag": _landing_diag(final), "reason": err}
                raise e
            e = _RegisterBlocked(err)
            e.diag = {"landing_diag": _landing_diag(final), "reason": err}
            raise e
        reg = resp.json()
        diag["register_s"] = round(time.time() - st["start"], 1)

        # send_otp
        send_url = reg.get("continue_url") or "https://auth.openai.com/api/accounts/email-otp/send"
        r = session.get(send_url, headers=session.auth_navigate_headers(referer=PASSWORD_REFERER),
                        allow_redirects=True)
        diag["send_otp"] = r.status_code

        # 收码(IMAP 快 / Graph 降级; 超时重发, 最多 otp_max_attempts 次)
        otp_after = st["start"]
        client = build_mail_client(account, proxy=resolved.session_url or None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"),
                                   cfg=cfg)
        identity = mail_identity_key(account)
        cache_path = resolve_path(cfg.get("mail", {}).get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
        used_cache = UsedCodeCache(cache_path)
        exclude = used_cache.seen_codes(identity)
        mail_cfg = cfg.get("mail", {})
        otp_timeout = int(mail_cfg.get("otp_wait", 150) or 150)
        otp_max_attempts = max(1, int(mail_cfg.get("otp_max_attempts", 2) or 2))
        otp = None
        for attempt in range(otp_max_attempts):
            try:
                otp = client.wait_for_otp(after_ts=otp_after, timeout=otp_timeout,
                                          interval=3, settle_seconds=5, exclude_codes=exclude)
                break
            except Exception as exc:
                if attempt >= otp_max_attempts - 1:
                    raise _OtpFailed(f"OTP 收码失败: {type(exc).__name__}: {exc}") from exc
                time.sleep(1)
                session.get(send_url, headers=session.auth_navigate_headers(referer=PASSWORD_REFERER),
                            allow_redirects=True)
                otp_after = time.time()
        used_cache.remember(identity, otp, email=email, status="submitted")
        diag["otp"] = otp
        diag["otp_s"] = round(time.time() - st["start"], 1)

        # validate
        auth.validate_email_otp(session, otp, None)

        # create_account: quickjs 真 t 与 browser 真 so 并行(独立资源)
        holder: dict[str, Any] = {}

        def _gen_t() -> None:
            _ct = time.time()
            try:
                # 静默 quickjs 默认 log(so_len 是 vm so, 会被忽略); so 由 browser 采集
                tok, _ = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_OAUTH, cfg=cfg,
                                                        log=lambda m: None)
                holder["tok2"] = tok
                print(f"  [quickjs/t] create 真 t 就绪 t_len={len(tok)} ({time.time()-_ct:.1f}s, so 由 browser 采集)")
            except Exception as exc:
                holder["t_err"] = f"{type(exc).__name__}: {exc}"
            holder["t_s"] = time.time() - _ct

        def _gen_so() -> None:
            _ct = time.time()
            so = None
            # 无 so 必死(测活实证), 采集失败重试 3 次, 仍失败主线程中止
            for _try in range(3):
                try:
                    br = harvest_browser_sentinel(cfg, flow=FLOW_OAUTH, device_id=session.device_id,
                                                  proxy=resolved.session_url, headless=True, timeout_s=90)
                    if br.get("ok") and br.get("so_header"):
                        so = br["so_header"]
                        break
                except Exception as exc:
                    holder["so_warn"] = f"{type(exc).__name__}: {str(exc)[:80]}"
                time.sleep(1)
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
        diag["create_parallel"] = round(time.time() - _ct0, 1)
        if holder.get("t_err"):
            raise _SessionFailed(f"quickjs t 生成失败: {holder['t_err']}")
        if not so_b:
            warn = holder.get("so_warn") or ""
            raise _SoFailed(f"browser so 采集失败(重试3次后仍无 so): {str(warn)[:120]}")

        h2 = session.auth_api_headers(referer=ABOUT_YOU_REFERER)
        h2["openai-sentinel-token"] = tok2
        h2["openai-sentinel-so-token"] = so_b
        resp2 = session.post(CREATE_URL, headers=h2, data=json.dumps({"name": name, "birthdate": bday}))
        diag["create_http"] = resp2.status_code
        diag["create_s"] = round(time.time() - st["start"], 1)
        if resp2.status_code != 200:
            raise _CreateFailed(f"create_account HTTP {resp2.status_code}: {resp2.text[:150]}")
        cr = resp2.json()
        cu = cr.get("continue_url") or cr.get("url")
        if not cu:
            raise _CreateFailed("create_account 无 continue_url")

        # callback + session
        auth.follow_oauth_callback(session, cu)
        info = auth.fetch_session(session)
        at = info.get("accessToken")
        if not at:
            raise _SessionFailed("session 无 accessToken")
        diag["session_s"] = round(time.time() - st["start"], 1)
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                    "secure": bool(getattr(c, "secure", False))}
                   for c in session.session.cookies.jar]
        return {
            "at": at,
            "device_id": session.device_id,
            "cookies": cookies,
            "refresh_token": info.get("refreshToken") or info.get("refresh_token") or "",
            "t_len": len(tok2),
            "so_len": len(so_b),
            "has_so": True,
            "proxy_used": resolved.upstream_url or resolved.session_url or "",
            "diag": diag,
        }, session, resolved
    except Exception:
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
        activated = False
        if enroll_secret and session_id and factor_id:
            import pyotp

            code6 = pyotp.TOTP(enroll_secret).now()
            resp_act = session.post(ACTIVATE_URL, headers=h6, data=json.dumps({
                "code": code6, "session_id": session_id,
                "factor_id": factor_id, "factor_type": "totp"}), timeout=30)
            activated = resp_act.status_code == 200 and '"success":true' in (resp_act.text or "")
            try:
                resp_info = session.get(MFA_INFO_URL, headers=h6, timeout=30)
                if '"mfa_enabled":true' in (resp_info.text or ""):
                    activated = True
            except Exception:
                pass
        if not activated:
            raise _EnrollFailed("activate_enrollment 未确认 mfa_enabled=true")
        return {"totp_secret": enroll_secret, "totp_enrolled": True}
    finally:
        pass  # session/resolved 由 register_account 统一关闭


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
            if not auto_retry or att >= 2 or "-sid-" not in (proxy_url or "") or "-t-" not in (proxy_url or ""):
                break
            new_sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            proxy_url = re.sub(r"-sid-[^-]+-t-", f"-sid-{new_sid}-t-", proxy_url, count=1)
            last_diag["retry_sid"] = att + 1
            time.sleep(1)
        except _SoFailed as exc:
            return RegistrationResult(RegisterOutcome.SO_FAILED, email, {"reason": str(exc)[:150]})
        except _OtpFailed as exc:
            return RegistrationResult(RegisterOutcome.OTP_FAILED, email, {"reason": str(exc)[:150]})
        except _CreateFailed as exc:
            return RegistrationResult(RegisterOutcome.CREATE_FAILED, email, {"reason": str(exc)[:150]})
        except _SessionFailed as exc:
            return RegistrationResult(RegisterOutcome.SESSION_FAILED, email, {"reason": str(exc)[:150]})
        except Exception as exc:
            return RegistrationResult(RegisterOutcome.SESSION_FAILED, email,
                                      {"reason": f"{type(exc).__name__}: {str(exc)[:150]}"})

    if reg is None:
        last_diag["elapsed_s"] = round(time.time() - t0, 1)
        return RegistrationResult(last_outcome, email, last_diag)

    mail_main = account.get("email") or ""
    # create 后即时健康检查(秒封检测) + 2FA 激活; 复用注册会话/隧道(贯穿整条链)
    try:
        _h_t0 = time.time()
        health = check_account_health(session, reg["at"])  # type: ignore[arg-type]
        _health_s = round(time.time() - _h_t0, 1)
        if health.get("status") != "ok":
            rec = _partial_record(reg, email, password, name, bday, mail_main, "health_failed")
            save_account(cfg, record=rec)
            return RegistrationResult(RegisterOutcome.HEALTH_FAILED, email,
                                      {"reason": f"health {health.get('status')} http={health.get('http')}"}, rec)
        _en_t0 = time.time()
        totp = _enroll_totp(cfg, session, reg)  # type: ignore[arg-type]
        record = _build_record(reg, email, password, name, bday, mail_main, totp)
        save_account(cfg, record=record)
        diag = dict(reg.get("diag") or {})
        diag["health_s"] = _health_s
        diag["enroll_s"] = round(time.time() - _en_t0, 1)
        diag["elapsed_s"] = round(time.time() - t0, 1)
        return RegistrationResult(RegisterOutcome.SUCCESS, email, diag, record)
    except _EnrollFailed as exc:
        rec = _partial_record(reg, email, password, name, bday, mail_main, "registered_no_totp")
        save_account(cfg, record=rec)
        return RegistrationResult(RegisterOutcome.ENROLL_FAILED, email,
                                  {"reason": str(exc)[:150]}, rec)
    finally:
        if resolved is not None:
            resolved.close()


def _partial_record(reg, email, password, name, bday, mail_main, status) -> dict[str, Any]:
    return {
        "email": email, "password": password, "access_token": reg["at"],
        "refresh_token": reg["refresh_token"], "device_id": reg["device_id"],
        "name": name, "birthdate": bday, "mail_main": mail_main,
        "status": status, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_cookies": reg["cookies"], "proxy_used": reg.get("proxy_used", ""),
    }


def _build_record(reg, email, password, name, bday, mail_main, totp) -> dict[str, Any]:
    rec = _partial_record(reg, email, password, name, bday, mail_main, "ok")
    rec["totp_secret"] = totp["totp_secret"]
    rec["sentinel_obs"] = {
        "challenge_mode": "quickjs_pwd_v3",
        "create_has_so": reg["has_so"],
        "create_so_len": reg["so_len"],
        "t_len": reg["t_len"],
        "flow": FLOW_PWD,
        "create_flow": FLOW_OAUTH,
        "totp_enrolled": True,
    }
    return rec

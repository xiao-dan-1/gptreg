"""OTP-only 注册(与 register_pwd 密码+TOTP 对称的并行路径, 经 cli.py 用)。

register_pwd = 密码注册+TOTP(主路线); register_otp = 纯邮箱 OTP 注册。
本模块含 register_one(单次) + run_batch(批量) + classify_result(失败分桶)。
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from gptreg import auth
from gptreg.config import random_birthdate, random_display_name, resolve_path
from gptreg.health import check_account_health
from gptreg.mail.mail_util import mail_identity_key
from gptreg.mail.pool import MailPool, choose_registration_email
from gptreg.mail.providers import UsedCodeCache
from gptreg.mail.wait_otp import wait_otp_with_retry

_ENROLL_URL = "https://chatgpt.com/backend-api/accounts/mfa/enroll"
_ACTIVATE_URL = "https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment"


def _set_password_via_reauth(cfg, session, email: str, account: dict, new_pw: str) -> str:
    """注册后 reauth 补设密码（auth.openai.com/api/accounts/password/add）。

    关键：signin 带 post_login_add_password=true 建立设密码事务（chatgpt 前端逆向参数）。
    流程: reauth signin → authorize(邮箱验证) → 收 OTP → validate → password/add。
    返回设置的密码(成功)或 ""(失败)。
    """
    from urllib.parse import urlencode

    from gptreg.mail.providers import build_mail_client

    try:
        mail_main = account["email"]
        auth.get_providers(session)
        csrf = auth.get_csrf_token(session)
        query = {
            "prompt": "login",
            "ext-oai-did": session.device_id,
            "reauth": "password",
            "max_age": "0",
            "login_hint": email,
            "screen_hint": "login_or_signup",
            "post_login_add_password": "true",
        }
        url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(query)
        h = session.chatgpt_headers()
        h["content-type"] = "application/x-www-form-urlencoded"
        h["origin"] = "https://chatgpt.com"
        body = urlencode({"callbackUrl": "https://chatgpt.com/", "csrfToken": csrf, "json": "true"})
        resp = session.post(url, headers=h, data=body, timeout=30)
        if resp.status_code != 200:
            logger.warning("[Password] reauth signin HTTP %s", resp.status_code)
            return ""
        auth_url = resp.json().get("url", "")
        if not auth_url:
            logger.warning("[Password] reauth 无 authorize url")
            return ""
        final = auth.follow_authorize(session, auth_url, attempts=2)
        if "email-verification" not in final:
            logger.warning("[Password] reauth 未落到邮箱验证: %s", final[:80])
            return ""
        # 收 reauth OTP（同一号池邮箱）
        mail_cfg = cfg.get("mail", {})
        client = build_mail_client(
            account,
            proxy=None,
            impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"),
            cfg=cfg,
        )
        otp = client.wait_for_otp(
            after_ts=time.time() - 3,
            timeout=int(mail_cfg.get("max_wait", 200)),
            interval=int(mail_cfg.get("poll_interval", 3)),
            settle_seconds=5,
        )
        sentinel_otp, _ = auth.make_sentinel_headers(session, None, "authorize_continue", source="pow")
        hdr = session.auth_api_headers(referer="https://auth.openai.com/email-verification")
        hdr["openai-sentinel-token"] = sentinel_otp
        sess_otp = session.post("https://auth.openai.com/api/accounts/email-otp/validate",
                                headers=hdr, data=json.dumps({"code": otp}), allow_redirects=False, timeout=30)
        if sess_otp.status_code != 200:
            logger.warning("[Password] reauth OTP validate HTTP %s", sess_otp.status_code)
            return ""
        # 设密码
        h4 = session.auth_api_headers(referer="https://auth.openai.com/")
        h4["content-type"] = "application/json"
        resp4 = session.post("https://auth.openai.com/api/accounts/password/add",
                             headers=h4, data=json.dumps({"password": new_pw}), timeout=30)
        if resp4.status_code == 200:
            logger.info("[Password] ✅ 补密码成功 (%s)", mail_main)
            return new_pw
        logger.warning("[Password] password/add HTTP %s: %s", resp4.status_code, (resp4.text or "")[:120])
        return ""
    except Exception as exc:
        logger.warning("[Password] reauth 补密码异常: %s", str(exc)[:120])
        return ""


def _enroll_totp_now(session, at: str, device_id: str, timeout: int = 30) -> dict:
    """注册后立即开 TOTP（用新鲜会话 token，对齐 gpt-free-register）。

    返回 {"totp_secret": str, "totp_enrolled": bool}；任何失败返回 totp_enrolled=False。
    """
    try:
        import pyotp

        h6 = session.chatgpt_headers(referer="https://chatgpt.com/")
        h6["authorization"] = f"Bearer {at}"
        h6["oai-device-id"] = device_id
        h6["content-type"] = "application/json"
        resp = session.post(_ENROLL_URL, headers=h6, data=json.dumps({"factor_type": "totp"}), timeout=timeout)
        if resp.status_code != 200:
            logger.warning("[TOTP] enroll HTTP %s: %s", resp.status_code, (resp.text or "")[:150])
            return {"totp_secret": "", "totp_enrolled": False}
        ej = resp.json()
        secret = str(ej.get("secret") or "")
        session_id = ej.get("session_id")
        factor_id = (ej.get("factor") or {}).get("id")
        if not (secret and session_id and factor_id):
            logger.warning("[TOTP] enroll 缺 secret/session_id/factor_id")
            return {"totp_secret": "", "totp_enrolled": False}
        code6 = pyotp.TOTP(secret).now()
        resp2 = session.post(_ACTIVATE_URL, headers=h6, data=json.dumps({
            "code": code6, "session_id": session_id,
            "factor_id": factor_id, "factor_type": "totp"}), timeout=timeout)
        ok = resp2.status_code == 200 and '"success":true' in (resp2.text or "")
        logger.info("[TOTP] activate_enrollment HTTP %s ok=%s", resp2.status_code, ok)
        return {"totp_secret": secret if ok else "", "totp_enrolled": ok}
    except Exception as exc:
        logger.warning("[TOTP] enroll 异常: %s", str(exc)[:100])
        return {"totp_secret": "", "totp_enrolled": False}


def _enroll_recovery_now(session, at: str, device_id: str, timeout: int = 30) -> dict:
    """开 recovery key(防 TOTP 锁死): enroll recovery_code → activate(提交整个 key)。

    2026-08-12 研究确认的纯协议流程:
      POST mfa/enroll {"factor_type": "recovery_code"} → {secret: <30字符key>, session_id, factor}
      POST mfa/user/activate_enrollment {code: <整个key>, ...} → success(必须提交整个 key,
      不是 TOTP 码/前6位——那都会 Invalid code)。
    返回 {"recovery_key": str, "recovery_enrolled": bool}。
    """
    try:
        h = session.chatgpt_headers(referer="https://chatgpt.com/settings/security")
        h["authorization"] = f"Bearer {at}"
        h["oai-device-id"] = device_id
        h["content-type"] = "application/json"
        resp = session.post(_ENROLL_URL, headers=h, data=json.dumps({"factor_type": "recovery_code"}), timeout=timeout)
        if resp.status_code != 200:
            logger.warning("[TOTP/recovery] enroll HTTP %s: %s", resp.status_code, (resp.text or "")[:150])
            return {"recovery_key": "", "recovery_enrolled": False}
        ej = resp.json()
        key = str(ej.get("secret") or "")
        session_id = ej.get("session_id")
        factor_id = (ej.get("factor") or {}).get("id")
        if not (key and session_id and factor_id):
            logger.warning("[TOTP/recovery] enroll 缺 key/session/factor")
            return {"recovery_key": "", "recovery_enrolled": False}
        resp2 = session.post(_ACTIVATE_URL, headers=h, data=json.dumps({
            "code": key, "session_id": session_id,
            "factor_id": factor_id, "factor_type": "recovery_code"}), timeout=timeout)
        ok = resp2.status_code == 200 and '"success":true' in (resp2.text or "")
        logger.info("[TOTP/recovery] activate HTTP %s ok=%s", resp2.status_code, ok)
        return {"recovery_key": key if ok else "", "recovery_enrolled": ok}
    except Exception as exc:
        logger.warning("[TOTP/recovery] enroll 异常: %s", str(exc)[:100])
        return {"recovery_key": "", "recovery_enrolled": False}
from gptreg.postlogin import post_login_warmup
from gptreg.proxyutil import resolve_proxy
from gptreg.session import BrowserSession
from gptreg.account_store import save_success

logger = logging.getLogger(__name__)

# 失败分桶：表观成功率勿把 OTP/TLS 与 create 拒建揉成一类
FAIL_BUCKETS = (
    "success",
    "create_disallow",  # create 400 registration_disallowed — 邮箱/身份主因
    "create_other",  # create 其它 4xx/5xx
    "otp_mail",  # 收信/OTP 超时或 Graph 拉信失败
    "tls_ssl",  # curl 35 / OPENSSL / SSL 基建
    "proxy",  # 代理解析/连通
    "pool_empty",
    "health_fail",  # 注册后 accounts/check 非 ok
    "session_fail",  # OAuth callback / session
    "other",
)


def _root(cfg: dict[str, Any]) -> Path:
    return Path(cfg.get("_root") or ".")


def classify_result(result: dict[str, Any] | None) -> str:
    """把单次 register/run_batch 结果归入失败桶（或 success）。"""
    if not result:
        return "other"
    if result.get("success"):
        return "success"
    if result.get("fail_bucket") in FAIL_BUCKETS:
        return str(result["fail_bucket"])

    err = str(result.get("error") or "")
    err_l = err.lower()
    create_acked = bool(result.get("create_acknowledged"))

    if "号池无可用" in err or "pool" in err_l and "empty" in err_l:
        return "pool_empty"
    if "registration_disallowed" in err_l:
        return "create_disallow"
    if re.search(r"create_account\s+http\s+400", err_l) and "registration_disallowed" in err_l:
        return "create_disallow"
    if "create_account" in err_l and ("http 4" in err_l or "http 5" in err_l or "400" in err):
        if "registration_disallowed" in err_l:
            return "create_disallow"
        return "create_other"
    if create_acked and ("http error 400" in err_l or "http 400" in err_l):
        # create 已 ack 后的 400 多为回调/session 阶段, 非 create 拒建。
        # 仅错误文本含 create 上下文才归 create_disallow, 否则归 session_fail
        if "create_account" in err_l:
            return "create_disallow"
        return "session_fail"
    if any(x in err_l for x in ("curl: (35)", "sslerror", "openssl", "tls connect", "tls ")):
        return "tls_ssl"
    if any(
        x in err
        for x in (
            "OTP",
            "MailClient",
            "等待",
            "超时",
            "access_token",
            "MSMail",
            "拉邮件",
        )
    ) or "mail" in err_l and ("timeout" in err_l or "otp" in err_l):
        return "otp_mail"
    if "proxy" in err_l or "代理" in err:
        return "proxy"
    if "健康检查" in err or "health" in err_l and "fail" in err_l:
        return "health_fail"
    if any(x in err for x in ("登录态", "session", "accessToken", "callback", "OAuth")):
        return "session_fail"
    if "HTTP Error 400" in err and not create_acked:
        # 可能是更早的 400；保守 other，避免误标
        return "other"
    return "other"


def summarize_buckets(results: list[dict[str, Any]]) -> dict[str, Any]:
    """批量结果分桶汇总：表观成功率 vs 到 create 的通过率。"""
    buckets = Counter(classify_result(r) for r in results)
    n = len(results)
    ok = buckets.get("success", 0)
    # 「有资格谈 create」= 成功 或 create_* 失败（协议已走到建号）
    reached_create = ok + buckets.get("create_disallow", 0) + buckets.get("create_other", 0)
    create_ok = ok  # 成功必过 create；health 失败若将来单独算可再拆
    # 基建噪声：未到 create
    infra = (
        buckets.get("otp_mail", 0)
        + buckets.get("tls_ssl", 0)
        + buckets.get("proxy", 0)
        + buckets.get("pool_empty", 0)
    )
    return {
        "n": n,
        "ok": ok,
        "apparent_pass_rate": (ok / n) if n else 0.0,
        "reached_create": reached_create,
        "create_pass_rate": (create_ok / reached_create) if reached_create else None,
        "infra_noise": infra,
        "buckets": dict(buckets),
    }


def format_bucket_summary(summary: dict[str, Any]) -> str:
    b = summary.get("buckets") or {}
    parts = [f"{k}={v}" for k, v in sorted(b.items(), key=lambda x: (-x[1], x[0]))]
    cpr = summary.get("create_pass_rate")
    cpr_s = f"{cpr:.0%}" if cpr is not None else "n/a"
    return (
        f"表观 {summary.get('ok', 0)}/{summary.get('n', 0)}"
        f"({summary.get('apparent_pass_rate', 0):.0%}) | "
        f"到create {summary.get('reached_create', 0)} 通过率 {cpr_s} | "
        f"基建噪声 {summary.get('infra_noise', 0)} | "
        + ", ".join(parts)
    )


def _finalize_session(session: BrowserSession, continue_url: str, email: str, attempts: int) -> dict:
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            logger.info("[登录态] OAuth 回调 + session (%s/%s) %s", i, attempts, email)
            auth.follow_oauth_callback(session, continue_url)
            time.sleep(1)
            info = auth.fetch_session(session)
            if not info.get("accessToken"):
                raise RuntimeError("session 无 accessToken")
            return info
        except Exception as exc:
            last_exc = exc
            if i >= attempts:
                break
            backoff = 2 ** (i - 1)
            logger.warning("[登录态] 失败，%.1fs 后重试: %s", backoff, exc)
            time.sleep(backoff)
    raise RuntimeError(f"登录态建立失败: {last_exc}") from last_exc


def register_one(
    cfg: dict[str, Any],
    account: dict[str, Any],
    *,
    proxy: str | None = None,
    name: str | None = None,
    birthdate: str | None = None,
    used_cache: UsedCodeCache | None = None,
    proxy_pool=None,
) -> dict[str, Any]:
    """执行一次完整 OTP-only 注册。

    主流程:
      providers → csrf → signin → authorize
      → sentinel(authorize_continue) → OTP validate
      → sentinel(oauth_create_account) → create_account
      → callback → /api/auth/session → 落盘

    proxy_pool: 提供 ProxyPool 时从池 acquire 隧道(复用, 免每次建隧道);
                None 则走 resolve_proxy(现场建, 兼容旧路径)。
    """
    mail_main = account["email"]  # 号池主邮箱 / 收码身份
    email, used_alias = choose_registration_email(account, cfg)  # 注册用（可 plus 别名）
    # 仅 Outlook(ms_oauth) 用别名；iCloud/cloudmail/api 用主邮箱(URL绑定/独立收件箱/API按主号拉码,
    # alias 收码不可靠)。对齐 batch_totp 策略。
    if account.get("mail_type") != "ms_oauth" and used_alias:
        email = mail_main
        used_alias = False
    display_name = name or cfg.get("register", {}).get("default_name") or random_display_name()
    bday = birthdate or random_birthdate(cfg)
    if proxy_pool is not None:
        resolved = proxy_pool.acquire()
        _from_pool = True
    else:
        resolved = resolve_proxy(cfg, proxy)
        _from_pool = False
    session = BrowserSession(cfg, proxy=resolved.session_url)
    session._proxy_label = resolved.label()
    create_acked = False
    create_attempts_log: list[dict[str, Any]] = []

    # 分阶段计时（monotonic 秒，成功/失败都落盘，便于定位基建卡点）
    _stage: dict[str, float] = {"start": time.monotonic()}

    def _mark(name: str) -> None:
        _stage[name] = time.monotonic()
        _stage["last"] = _stage[name]

    def _elapsed(earlier: str, later: str) -> float | None:
        if earlier in _stage and later in _stage:
            return round(_stage[later] - _stage[earlier], 1)
        return None

    logger.info(
        "[注册] 开始 reg=%s main=%s alias=%s proxy=%s mail_type=%s",
        email,
        mail_main,
        used_alias,
        resolved.label(),
        account.get("mail_type"),
    )
    logger.debug(
        "[注册] device_id=%s sid=%s region=%s",
        session.device_id,
        resolved.sid,
        resolved.region,
    )

    try:
        # otp_after 必须在 signin 之前抓：OTP 邮件可在 signin_flow 期间就到件(Outlook 实测 <1s),
        # 若 signin 后抓, 邮件时间戳 < after_ts 会被 wait_for_otp 当旧件过滤 → 永远收不到码。
        otp_after = time.time()
        auth.signin_flow(session, email, follow_sleep=1.5)
        _mark("authorize_done")

        # OTP 阶段 sentinel：始终 pow（Jennifer OTP 无 so；browser 留给 create）
        sentinel_otp, _ = auth.make_sentinel_headers(
            session, None, "authorize_continue", source="pow"
        )
        time.sleep(0.2)

        mail_cfg = cfg.get("mail", {})
        # 共享收码: 代理决策(仅 ms_oauth 走隧道) + UsedCodeCache + wait_for_otp
        otp, _otp_extra = wait_otp_with_retry(
            cfg, account, email=email,
            after_ts=otp_after, proxy_url=resolved.session_url or None,
            max_attempts=1,
            timeout=int(mail_cfg.get("max_wait", 90)),
            interval=int(mail_cfg.get("poll_interval", 3)),
            settle_seconds=int(mail_cfg.get("settle_seconds", 5)),
        )
        _mark("otp_got")
        logger.info("[OTP] 拿到验证码 %s，提交中", email)

        validate_result = auth.validate_email_otp(session, otp, sentinel_otp)
        auth.maybe_follow_external(session, validate_result)
        time.sleep(0.5)
        # k12: validate 后先访问 about-you 推进 session，降低 invalid_auth_step
        auth.warm_about_you(session)
        time.sleep(0.3)

        # create_account：pow 默认；可选同 body 重试 + disallow 后当次 browser 回退
        reg_cfg = cfg.get("register") or {}
        create_retries = max(1, int(reg_cfg.get("create_retries", 3) or 3))
        create_retry_sleep = float(reg_cfg.get("create_retry_sleep", 2.0) or 2.0)
        browser_fallback = bool(reg_cfg.get("create_browser_fallback", False))
        base_source = str((cfg.get("protocol") or {}).get("sentinel_source") or "pow").strip().lower()
        if base_source in {"browser", "pw", "playwright", "chrome"}:
            base_source = "browser"
        elif base_source in {"node", "node_vm", "nodepow"}:
            base_source = "node"
        elif base_source in {"quickjs", "qjs"}:
            base_source = "quickjs"
        elif base_source in {"browser_t_quickjs_so", "bt_vs", "btqs", "true_t_vm_so"}:
            base_source = "browser_t_quickjs_so"
        elif base_source in {"quickjs_t_browser_so", "qt_bs", "qtbs", "vm_t_true_so"}:
            base_source = "quickjs_t_browser_so"
        elif base_source in {"quickjs_pwd_v3", "pwd", "pwd_v3"}:
            base_source = "quickjs_pwd_v3"
        else:
            base_source = "pow"

        create_result = None
        create_last_err: Exception | None = None
        has_so = False
        so_len = 0
        challenge_mode = base_source
        sentinel_meta: dict[str, Any] = {}
        create_attempts_log = []

        def _one_create_wave(source: str, retries: int | None = None) -> dict[str, Any]:
            nonlocal has_so, so_len, challenge_mode, sentinel_meta, create_last_err
            wave_retries = max(1, int(retries if retries is not None else create_retries))
            sentinel_create, so_header = auth.make_sentinel_headers(
                session, None, "oauth_create_account", require_so=False, source=source
            )
            # 隔离实验：--no-so 剥掉 so 头（判断 so 对存活的影响）
            if reg_cfg.get("no_so"):
                so_header = None
            has_so = bool(so_header)
            so_len = len(so_header or "")
            sentinel_meta = getattr(session, "_last_sentinel_meta", None) or {}
            challenge_mode = str(sentinel_meta.get("mode") or source or "pow")
            logger.info(
                "[Sentinel/obs] create flow=oauth_create_account mode=%s has_so=%s so_len=%s t_len=%s",
                challenge_mode,
                has_so,
                so_len,
                sentinel_meta.get("t_len"),
            )
            last_exc: Exception | None = None
            for attempt in range(wave_retries):
                time.sleep(0.2 if attempt == 0 else create_retry_sleep)
                try:
                    result = auth.create_account(
                        session,
                        display_name,
                        bday,
                        sentinel_create,
                        so_header,
                        require_so=False,
                    )
                    create_attempts_log.append(
                        {
                            "source": source,
                            "attempt": attempt + 1,
                            "ok": True,
                            "has_so": has_so,
                            "so_len": so_len,
                        }
                    )
                    return result
                except Exception as exc:
                    last_exc = exc
                    err_s = f"{type(exc).__name__}: {exc}"
                    is_disallow = "registration_disallowed" in err_s
                    create_attempts_log.append(
                        {
                            "source": source,
                            "attempt": attempt + 1,
                            "ok": False,
                            "disallow": is_disallow,
                            "error": err_s[:240],
                            "has_so": has_so,
                            "so_len": so_len,
                        }
                    )
                    if is_disallow and attempt < wave_retries - 1:
                        # 资料 zip：同 body 重试；不换 name/邮箱、不改 sentinel 内容
                        logger.warning(
                            "[Auth] registration_disallowed，同 body 重试 (%s/%s) mode=%s",
                            attempt + 1,
                            wave_retries,
                            source,
                        )
                        continue
                    break
            create_last_err = last_exc
            if last_exc:
                raise last_exc
            raise RuntimeError("create_account failed without exception")

        try:
            # 回退开启时 pow 波次只试 1 次：pow 空 t 已被 create 拒（2026-08 实测），
            # 保留 3 次同 body 重试纯属浪费，直接让 browser 波次接棒产真 t。
            if browser_fallback and base_source == "pow":
                create_result = _one_create_wave(base_source, retries=1)
            else:
                create_result = _one_create_wave(base_source)
        except Exception as exc:
            err_s = f"{type(exc).__name__}: {exc}"
            can_fallback = (
                browser_fallback
                and base_source == "pow"
                and "registration_disallowed" in err_s
            )
            if can_fallback:
                logger.warning(
                    "[Auth] pow create disallowed 后当次 browser 回退（create_browser_fallback=true）"
                )
                # browser 只试 1 次：真 Chrome token() 产真 t/so；同根 identity 连刷无意义
                create_result = _one_create_wave("browser", retries=1)
            else:
                raise

        create_acked = True
        _mark("create_done")
        continue_url = (create_result or {}).get("continue_url")
        if not continue_url:
            raise RuntimeError(f"create_account 无 continue_url: {create_result}")

        session_info = _finalize_session(
            session,
            continue_url,
            email,
            attempts=int(cfg.get("register", {}).get("finalize_attempts", 5)),
        )
        access_token = session_info["accessToken"]

        # 注册后即时健康检查：秒封不算成功。create 已 200 后瞬时网络抖动不应丢弃整号 → 重试 3 次
        health: dict[str, Any] = {}
        health_status = "error"
        for _hc in range(3):
            health = check_account_health(session, access_token)
            health_status = health.get("status") or "error"
            if health_status == "ok":
                break
            logger.warning("[Auth] 健康检查 status=%s (重试 %s/3)", health_status, _hc + 1)
            time.sleep(1.5 * (_hc + 1))
        if health_status != "ok":
            raise RuntimeError(
                f"注册后健康检查失败 status={health_status} "
                f"http={health.get('http')} body={(health.get('body') or health.get('detail') or '')[:180]}"
            )
        _mark("health_done")

        # Step B2：注册后立即开 TOTP 2FA（config register.enable_totp=true 开启）
        # 用注册会话新鲜 token（对齐 gpt-free-register：OTP-only + TOTP 交付形态）。
        # 若 recent_auth_required（token 不够新鲜）则记录未开，账号仍正常交付。
        totp: dict[str, Any] = {"totp_secret": "", "totp_enrolled": False}
        if bool((cfg.get("register") or {}).get("enable_totp", False)):
            totp = _enroll_totp_now(session, access_token, session.device_id)
            if totp.get("totp_enrolled"):
                logger.info("[TOTP] ✅ OTP-only 账号 TOTP 已激活 (secret_len=%s)", len(str(totp.get("totp_secret") or "")))
                # 同步开 recovery key(防 TOTP 锁死; 需 fresh token, 刚激活 TOTP 满足)
                recovery = _enroll_recovery_now(session, access_token, session.device_id)
                if recovery.get("recovery_enrolled"):
                    totp["recovery_key"] = recovery["recovery_key"]
                    logger.info("[TOTP/recovery] ✅ recovery key 已激活")
                else:
                    logger.warning("[TOTP/recovery] recovery 未激活(不影响 TOTP 交付)")
            else:
                logger.warning("[TOTP] 未激活（token 可能不够新鲜，可后续 reauth 补开）")

        # Step B3：注册后 reauth 补设密码（config register.enable_password=true 开启）
        # 纯协议补密码：signin 带 post_login_add_password=true → 邮箱 OTP → password/add。
        # 直接产出 email----password----2fa 全凭据（无需浏览器）。
        set_password = ""
        if bool((cfg.get("register") or {}).get("enable_password", False)):
            from gptreg.config import pick_password

            pw = pick_password(cfg)  # 统一密码(config)或随机
            set_password = _set_password_via_reauth(cfg, session, email, account, pw)
            if set_password:
                logger.info("[Password] ✅ OTP-only 账号补密码成功, 交付 password+2fa")
            else:
                logger.warning("[Password] 补密码失败（账号仍可交付无密码+TOTP）")

        # Step B：post-login 最小集（默认关；config register.post_login=true 开启）
        # so 策略不变；不造假 finalize/pow/turnstile
        post_login_enabled = bool((cfg.get("register") or {}).get("post_login", False))
        post_login_detail: dict[str, Any] | None = None
        if post_login_enabled:
            post_login_detail = post_login_warmup(session, access_token, session_info)

        chatreq_obs = sentinel_meta.get("chatreq") or getattr(session, "_last_chatreq_obs", None)
        timing = {
            "total_s": _elapsed("start", "health_done"),
            "signin_authorize_s": _elapsed("start", "authorize_done"),
            "otp_wait_s": _elapsed("authorize_done", "otp_got"),
            "validate_create_s": _elapsed("otp_got", "create_done"),
            "finalize_health_s": _elapsed("create_done", "health_done"),
        }
        sentinel_obs = {
            "flow": "oauth_create_account",
            "challenge_mode": challenge_mode,
            "has_so": has_so,
            "so_len": so_len,
            "t_len": sentinel_meta.get("t_len"),
            "browser_elapsed_s": sentinel_meta.get("elapsed_s"),
            "sdk_keys": sentinel_meta.get("sdk_keys"),
            "chatreq": chatreq_obs,
            "create_attempts": create_attempts_log,
            "post_login": post_login_enabled,
            "post_login_ok": bool((post_login_detail or {}).get("ok")) if post_login_enabled else None,
            "post_login_detail": post_login_detail,
            "timing_s": timing,
        }
        # 序列化会话 cookies 供日后 refresh(有 cookies 可无限刷新,见 store.refresh_token)
        # 注意:curl_cffi Cookies 直接迭代返回 str(名),必须走 .jar(http.cookiejar)拿 Cookie 对象
        try:
            sess_cookies = [
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                    "secure": bool(getattr(c, "secure", False)),
                    "expires": getattr(c, "expires", None),
                }
                for c in session.session.cookies.jar
            ]
        except Exception:
            sess_cookies = []
        out_dir = save_success(
            cfg,
            email=email,
            access_token=access_token,
            account=account,
            session_info=session_info,
            proxy_used=resolved.upstream_url or resolved.session_url,
            device_id=session.device_id,
            name=display_name,
            birthdate=bday,
            extra={"sentinel_obs": sentinel_obs, "health": health_status,
                   "totp_secret": totp.get("totp_secret") or "",
                   "totp_enrolled": bool(totp.get("totp_enrolled")),
                   "recovery_key": totp.get("recovery_key") or "",
                   "recovery_enrolled": bool(totp.get("recovery_enrolled")),
                   "password": set_password},
            session_cookies=sess_cookies,
        )
        used_cache.remember(mail_identity_key(account), otp, email=email, status="ok")
        logger.info(
            "[完成] %s token=%s... out=%s health=%s has_so=%s so_len=%s post_login=%s total=%ss",
            email,
            access_token[:16],
            out_dir,
            health_status,
            has_so,
            so_len,
            post_login_enabled,
            timing.get("total_s") or "?",
        )
        return {
            "success": True,
            "email": email,
            "mail_main": mail_main,
            "used_alias": used_alias,
            "access_token": access_token,
            "name": display_name,
            "birthdate": bday,
            "device_id": session.device_id,
            "proxy": resolved.upstream_url or resolved.session_url,
            "proxy_label": resolved.label(),
            "proxy_sid": resolved.sid,
            "proxy_region": resolved.region,
            "health": health_status,
            "sentinel_obs": sentinel_obs,
        }

    except Exception as exc:
        err_s = f"{type(exc).__name__}: {exc}"
        partial = {
            "success": False,
            "email": email,
            "mail_main": mail_main,
            "used_alias": used_alias,
            "error": err_s,
            "create_acknowledged": create_acked,
            "proxy_label": resolved.label(),
        }
        # create 重试/回退日志（若已进入 create 波次）
        if create_attempts_log:
            partial["create_attempts"] = create_attempts_log
        bucket = classify_result(partial)
        partial["fail_bucket"] = bucket
        if bucket == "create_disallow":
            partial["mailbox_note"] = "create_disallow_not_mailbox_ban"
        # 失败也要带阶段耗时：定位是 OTP 卡住 / TLS / create 拒建 / 登录态
        if _stage.get("last"):
            partial["elapsed_s"] = round(_stage["last"] - _stage["start"], 1)
            partial["stage_last"] = max(
                (k for k in ("authorize_done", "otp_got", "create_done", "health_done") if k in _stage),
                key=lambda k: _stage[k],
                default=None,
            )
        logger.error(
            "[失败] %s bucket=%s stage=%s elapsed=%ss: %s",
            email,
            bucket,
            partial.get("stage_last") or "-",
            partial.get("elapsed_s") or "?",
            err_s,
        )
        return partial
    finally:
        if _from_pool:
            proxy_pool.release(resolved)
        else:
            resolved.close()
        try:
            session.close()
        except Exception:
            pass


def run_batch(
    cfg: dict[str, Any],
    *,
    count: int = 1,
    workers: int = 1,
    delay: float = 0.0,
    continue_on_fail: bool = False,
    proxy: str | None = None,
    pool: MailPool | None = None,
) -> list[dict[str, Any]]:
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    # 代理池: workers>1 + 动态代理 + pool_size 配置时, 预建隧道池并发各取一条
    # (免每次建隧道开销 + 固定并发出口数分散 IP)。失败退回每次 resolve_proxy。
    proxy_pool = None
    _dyn = (cfg.get("proxy") or {}).get("dynamic") or {}
    _pool_size = int((cfg.get("proxy") or {}).get("pool_size") or _dyn.get("pool_size") or 0)
    if workers > 1 and not proxy and _dyn.get("enabled") and _pool_size > 0:
        try:
            from gptreg.proxyutil import ProxyPool

            proxy_pool = ProxyPool(cfg, size=min(workers, _pool_size))
            logger.info("[批量] 代理池预建完成 size=%s idle=%s", proxy_pool.size(), proxy_pool.idle())
        except Exception as exc:
            logger.warning("[批量] 建代理池失败(退回每次 resolve_proxy): %s", str(exc)[:100])
            proxy_pool = None

    mail_cfg = cfg.get("mail", {})
    pool_path = resolve_path(mail_cfg.get("pool_file", "mail_pool.txt"), _root(cfg))
    mail_pool = pool or MailPool(pool_path)
    if pool is None:
        n = mail_pool.load()
        logger.info("[号池] 加载 %s 条 %s", n, mail_pool.stats())

    cache = UsedCodeCache(
        resolve_path(mail_cfg.get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
    )
    results: list[dict[str, Any]] = []

    def one_job(index: int) -> dict[str, Any]:
        account = mail_pool.claim()
        if not account:
            return {
                "success": False,
                "error": "号池无可用邮箱",
                "email": "",
                "fail_bucket": "pool_empty",
            }
        email = account["email"]
        logger.info("[批量] #%s 领取 %s", index + 1, email)

        def _attempt(tag: str) -> dict[str, Any]:
            """执行一次注册并返回结果(不标记号池——占用保持到 one_job 结束,
            避免重试期间 mark_failed 释放 in_flight, 并发 worker 重复 claim 同主号)。"""
            try:
                res = register_one(cfg, account, proxy=proxy, used_cache=cache, proxy_pool=proxy_pool)
            except Exception as exc:
                partial = {"success": False, "email": email, "error": str(exc)}
                partial["fail_bucket"] = classify_result(partial)
                partial["retry_tag"] = tag
                return partial
            res["fail_bucket"] = classify_result(res)
            res["retry_tag"] = tag
            return res

        result = _attempt("first")
        bucket = result.get("fail_bucket")
        # 基建类失败（TLS/代理/OTP 超时）当次换 IP 重试一次：
        # register_one 内部每次重新 resolve_proxy，重跑即换 sid/IP。
        if not result.get("success") and bucket in ("tls_ssl", "proxy", "otp_mail"):
            logger.warning(
                "[批量] #%s %s 失败(%s)，换 IP 重试一次: %s",
                index + 1,
                email,
                bucket,
                str(result.get("error"))[:80],
            )
            result = _attempt("retry")
        # 统一标记号池(重试期间 in_flight 保持, 此处才释放; 防并发重复 claim)
        bucket = result.get("fail_bucket")
        if result.get("success"):
            mail_pool.mark_used(email)
        elif result.get("create_acknowledged"):
            # create 已 200 但后续失败。瞬时基建失败(网络/超时)不烧邮箱 → mark_failed 可重试;
            # 明确账号占用(已存在/吊销)才 mark_bad 永久弃号。
            if bucket in ("tls_ssl", "proxy", "otp_mail"):
                mail_pool.mark_failed(email)
            else:
                mail_pool.mark_bad(email, reason=result.get("error", ""))
        elif bucket == "create_disallow":
            # OpenAI 拒建号 ≠ 邮箱封死; OTP 往往仍通。记 fail 进 retrying, 勿 mark_bad
            mail_pool.mark_failed(email)
            result["mailbox_note"] = "create_disallow_not_mailbox_ban"
        else:
            mail_pool.mark_failed(email)
        return result

    if workers <= 1:
        for i in range(count):
            r = one_job(i)
            results.append(r)
            if not r.get("success") and not continue_on_fail:
                logger.error("[批量] 失败停止；可加 --continue-on-fail")
                break
            if delay > 0 and i < count - 1:
                time.sleep(delay)
        summary = summarize_buckets(results)
        logger.info("[汇总/分桶] %s", format_bucket_summary(summary))
        # 常驻浏览器池(klsf)生命周期：批量结束关池（幂等，fresh 路径无池时无操作）
        from gptreg.browser_pool import shutdown_all

        shutdown_all()
        return results

    # 并发
    logger.info("[批量] 并发 workers=%s count=%s", workers, count)
    future_map: dict = {}
    next_i = 0
    stop = False
    # 常驻浏览器池(klsf)：并发 worker 时按 workers 设池大小（上限由 config 控制），批量结束关池
    from gptreg.browser_pool import get_pool, shutdown_all

    if bool(((cfg.get("protocol") or {}).get("sentinel_browser_reuse"))):
        try:
            get_pool(cfg).set_pool_size(min(workers, max(1, int((cfg.get("protocol") or {}).get("sentinel_browser_pool_size") or 2))))
        except Exception as exc:
            logger.warning("[批量] 设浏览器池大小失败: %s", exc)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gptreg") as ex:
        def submit_one() -> bool:
            nonlocal next_i
            if stop or next_i >= count:
                return False
            fut = ex.submit(one_job, next_i)
            future_map[fut] = next_i
            next_i += 1
            if delay > 0 and next_i < count:
                time.sleep(delay)
            return True

        for _ in range(min(workers, count)):
            submit_one()
        while future_map:
            done, _ = wait(future_map.keys(), return_when=FIRST_COMPLETED)
            for fut in done:
                future_map.pop(fut, None)
                try:
                    r = fut.result()
                except Exception as exc:
                    r = {"success": False, "error": str(exc)}
                    r["fail_bucket"] = classify_result(r)
                results.append(r)
                if not r.get("success") and not continue_on_fail:
                    stop = True
                if not stop:
                    submit_one()
    # 常驻浏览器池(klsf)生命周期：批量结束关池（幂等）
    shutdown_all()
    if proxy_pool is not None:
        proxy_pool.close()
        logger.info("[批量] 代理池已关闭")
    summary = summarize_buckets(results)
    logger.info("[汇总/分桶] %s", format_bucket_summary(summary))
    return results

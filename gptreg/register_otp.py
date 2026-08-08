"""OTP-only 注册(与 register_pwd 密码+TOTP 对称的并行路径, 经 cli.py 用)。

register_pwd = 密码注册+TOTP(主路线); register_otp = 纯邮箱 OTP 注册。
本模块含 register_one(单次) + run_batch(批量) + classify_result(失败分桶)。
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from gptreg import auth
from gptreg.config import random_birthdate, random_display_name, resolve_path
from gptreg.health import check_account_health
from gptreg.mail.pool import MailPool, choose_registration_email
from gptreg.postlogin import post_login_warmup
from gptreg.mail.providers import UsedCodeCache, build_mail_client, mail_identity_key
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
) -> dict[str, Any]:
    """执行一次完整 OTP-only 注册。

    主流程:
      providers → csrf → signin → authorize
      → sentinel(authorize_continue) → OTP validate
      → sentinel(oauth_create_account) → create_account
      → callback → /api/auth/session → 落盘
    """
    mail_main = account["email"]  # 号池主邮箱 / 收码身份
    email, used_alias = choose_registration_email(account, cfg)  # 注册用（可 plus 别名）
    display_name = name or cfg.get("register", {}).get("default_name") or random_display_name()
    bday = birthdate or random_birthdate(cfg)
    resolved = resolve_proxy(cfg, proxy)
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
        auth.signin_flow(session, email, follow_sleep=1.5)
        otp_after = time.time()
        _mark("authorize_done")

        # OTP 阶段 sentinel：始终 pow（Jennifer OTP 无 so；browser 留给 create）
        sentinel_otp, _ = auth.make_sentinel_headers(
            session, None, "authorize_continue", source="pow"
        )
        time.sleep(0.2)

        mail_cfg = cfg.get("mail", {})
        browser = cfg.get("browser", {})
        client = build_mail_client(
            account,
            proxy=resolved.session_url or None,
            impersonate=browser.get("impersonate", "chrome142"),
            cfg=cfg,
        )
        identity = mail_identity_key(account)
        if used_cache is None:
            cache_path = resolve_path(
                mail_cfg.get("used_code_cache", "data/used_otp_codes.json"),
                _root(cfg),
            )
            used_cache = UsedCodeCache(cache_path)
        exclude = used_cache.seen_codes(identity)

        def _on_poll(info: dict) -> None:
            if info.get("excluded"):
                logger.info("[OTP] API 返回已排除旧码 %s，继续等", info.get("code"))
            else:
                logger.debug("[OTP] 候选码 %s source=%s", info.get("code"), info.get("source"))

        logger.info("[OTP] 等待验证码 %s exclude=%s", email, len(exclude))
        otp = client.wait_for_otp(
            after_ts=otp_after,
            timeout=int(mail_cfg.get("max_wait", 90)),
            interval=int(mail_cfg.get("poll_interval", 3)),
            settle_seconds=int(mail_cfg.get("settle_seconds", 5)),
            exclude_codes=exclude,
            on_poll=_on_poll,
        )
        # 先记住再提交，无论成败都排除，避免共享收件箱 stale 重放
        used_cache.remember(identity, otp, email=email, status="submitted")
        _mark("otp_got")
        logger.info("[OTP] 拿到验证码，提交中")

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
            extra={"sentinel_obs": sentinel_obs, "health": health_status},
            session_cookies=sess_cookies,
        )
        used_cache.remember(identity, otp, email=email, status="ok")
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
                res = register_one(cfg, account, proxy=proxy, used_cache=cache)
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
        return results

    # 并发
    logger.info("[批量] 并发 workers=%s count=%s", workers, count)
    future_map: dict = {}
    next_i = 0
    stop = False
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
    summary = summarize_buckets(results)
    logger.info("[汇总/分桶] %s", format_bucket_summary(summary))
    return results

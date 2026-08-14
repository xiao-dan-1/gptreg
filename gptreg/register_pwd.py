"""密码注册 + TOTP 2FA 核心（结构化结果，供 CLI/批量复用）。

主路线整条链：signin→authorize→register(设密码)→OTP 收码→validate→
create_account(quickjs 真 t + browser 真 so 并行)→session→mfa/enroll→
activate_enrollment(2FA 真激活)→构造落盘 record。

返回结构化 RegistrationResult(outcome/diag/record)，取代脚本里 print 判定：
CLI 与 batch_totp 共享本模块，失败类型决定主号"可重试 vs 永久弃用"。
"""
from __future__ import annotations

import contextvars
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
from gptreg.session import BrowserSession
from gptreg.account_store import save_account

logger = logging.getLogger(__name__)

FLOW_PWD = "username_password_create"
FLOW_OAUTH = "oauth_create_account"


def _rk_sentinel(session, device_id, flow, cfg, with_so=True):
    """register-kit token(flow) 完整流程替代手动 requirements/solve(英文时区名对齐)。

    设密码(FLOW_PWD)用 with_so=False; create(FLOW_OAUTH)用 with_so=True。
    返回 (token, so): token=JSON 字符串(p/t/c/id/flow), so=sessionObserverToken 字符串。
    """
    from gptreg.rk_sentinel import ensure_sentinel_proxy, gen_sentinel_pair, gen_sentinel_token
    ensure_sentinel_proxy(exit_proxy="socks5://127.0.0.1:10808")
    b = cfg.get("browser") or {}
    user_agent = b.get("user_agent") or ""
    language = b.get("language") or "en-US"
    languages = b.get("languages") or "en-US,en;q=0.9"
    width = int(b.get("screen_width") or 1920)
    height = int(b.get("screen_height") or 1080)
    cores = int(b.get("hardware_concurrency") or 16)
    timezone = b.get("timezone") or "America/Los_Angeles"
    if with_so:
        return gen_sentinel_pair(device_id, flow, user_agent,
                                 language=language, languages=languages,
                                 width=width, height=height, cores=cores, timezone=timezone)
    token = gen_sentinel_token(device_id, flow, user_agent,
                               language=language, languages=languages,
                               width=width, height=height, cores=cores, timezone=timezone)
    return token, None
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
    MAIL_CONFLICT = "mail_conflict"  # register 400 invalid_auth_step/Invalid authorization: 邮箱已推进过注册流程(状态机不可重入) → 永久弃用
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


class _MailStateConflict(RuntimeError):
    """register 400 + invalid_auth_step/Invalid authorization：邮箱已推进过注册流程
    (OTP 已消费/register 已设密码), OpenAI 状态机不可重入 → 永久弃用, 换 IP 无效。
    """


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
    account: dict[str, Any],
    email: str,
    password: str,
    final: str,
    st: dict[str, float],
    diag: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Stage 2: register(设密码) + send_otp。返回 (reg, send_url)。

    400 分类: 落 log-in=邮箱已注册(永久弃用) / invalid_auth_step|Invalid authorization
    =邮箱状态冲突(已推进过注册, 不可重入, 永久弃用) / 其他=IP 信誉(换 sid 可重试)。
    account 用于 email-verification 预验证时收码(新流程: 先验证邮箱再注册)。
    """
    _t0 = time.time()
    # 静默 quickjs 默认 log(其 so_len 是 vm so, 密码 register 无 so), 自行明确打印
    token, _ = _rk_sentinel(session, session.device_id, FLOW_PWD, cfg, with_so=False)
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
        # ⭐ email-verification 落点 + register 400: OpenAI 新流程要求"先验证邮箱"再注册。
        # authorize 落 email-verification 即已触发验证码发送(reauth 实证), 收码 validate 后
        # 重试 register; 预验证无效(真 IP 信誉/邮箱已注册)时收码超时或仍 400 → 回退原判定。
        if "email-verification" in (final or "") and "log-in" not in (final or "") and "/login" not in (final or ""):
            _ev_t0 = time.time()
            try:
                logger.warning("[Auth] email-verification 落点 register 400, 预验证邮箱后重试")
                _mc = cfg.get("mail") or {}
                # register 400 会推进 state(类似 register-kit 设密码重置 state),
                # signin 阶段那轮码作废 → 须主动 send_otp 重发再收(实测直接收超时 45s 无码)
                try:
                    _sr = session.get(
                        "https://auth.openai.com/api/accounts/email-otp/send",
                        headers=session.auth_navigate_headers(referer="https://auth.openai.com/email-verification"),
                        allow_redirects=True)
                    diag["pre_verify_send"] = _sr.status_code
                    logger.warning("[Auth] 预验证 send_otp HTTP %s", _sr.status_code)
                except Exception as exc:
                    diag["pre_verify_send"] = f"exc:{type(exc).__name__}"
                # 实证(2026-08-13 w=8): register 400 邮箱 send_otp 返回 200 但实际不发码
                # (服务端对该邮箱/IP 是硬性拒绝, 假成功)——收码必然超时, 属纯浪费。
                # 保留 20s 短窗口试错(万一真发码可治愈), 超时快速回退原判定。
                _otp, _extra = wait_otp_with_retry(
                    cfg, account, email=email, after_ts=st["start"],
                    proxy_url=None, session=session,
                    max_attempts=1, timeout=min(int(_mc.get("otp_wait", 150) or 150), 20),
                    interval=3, settle_seconds=5,
                )
                if _otp:
                    auth.validate_email_otp(session, _otp, None)
                    auth.warm_about_you(session)
                    resp = session.post(REGISTER_URL, headers=headers,
                                        data=json.dumps({"username": email, "password": password}))
                    diag["pre_verify_s"] = round(time.time() - _ev_t0, 1)
                    if resp.status_code == 200:
                        logger.info("[Auth] 预验证邮箱后 register 成功(新流程通过)")
            except Exception as exc:
                logger.warning("[Auth] email-verification 预验证失败(回退原判定): %s", str(exc)[:100])
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
        # 邮箱状态冲突(状态机不可重入): invalid_auth_step(warm 重试后仍 400)/Invalid authorization
        # = 邮箱已推进过注册流程(OTP 已消费/密码已设)。重跑同一邮箱必 400, 换 IP 无效 → 永久弃用。
        # 若误判 IP_BLOCKED, batch 会换 sid 反复戳同一邮箱, 放大认证请求量, 反易触发 rate_limit。
        _low = (resp.text or "").lower()
        if "invalid_auth_step" in _low or "invalid authorization" in _low or "invalid_authorization" in _low:
            e = _MailStateConflict(err)
            e.diag = {
                **diag,  # 合并 signin_s 等耗时字段, 让 [耗时] 归因可见(不再只有 landing/reason)
                "landing_diag": "邮箱已推进过注册流程(OTP 已消费/密码已设), 状态机不可重入, 弃用",
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
    # XDAuv 服务端偶发 Login failed(并发/高负载时更频繁), 提高重试容忍度
    otp_max_attempts = max(1, int(mail_cfg.get("otp_max_attempts", 3) or 3))
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
    _proto = cfg.get("protocol") or {}
    # so 来源(browser/quickjs/none): 密码模式 so 对照实验(2026-08-12)。
    # none=不发 so 头; quickjs=vm so; browser(默认)=真浏览器 so。
    so_source = str(_proto.get("sentinel_so_source") or "browser").strip().lower()
    holder["so_source"] = so_source

    _ct0 = time.time()
    if so_source in ("quickjs", "none"):
        # ⭐ 效率优化(2026-08-13): vm so 的 t 与 so 同一次 quickjs solve 产出
        # (同一 fp/challenge), 单次调用同时拿到两者 —— 旧实现按 t/so 各调一次完整
        # solve(重复计算 ~5s/号); 合并后 t/so 同源(同 c/device_id)反而更保真。
        # none 模式同样单次(只要 t, so 丢弃)。
        _ct = time.time()
        try:
            tok, so_vm = _rk_sentinel(session, session.device_id, FLOW_OAUTH, cfg, with_so=True)
            holder["tok2"] = tok
            holder["so_b"] = so_vm if so_source == "quickjs" else None
            logger.info("  [quickjs] create 真 t+so 一次产出 t_len=%s so_len=%s (%.1fs)",
                        len(tok), len(so_vm) if so_vm else 0, time.time() - _ct)
        except Exception as exc:
            holder["t_err"] = f"{type(exc).__name__}: {exc}"
        holder["t_s"] = holder["so_s"] = time.time() - _ct
    else:
        def _gen_t() -> None:
            _ct = time.time()
            try:
                # 静默 quickjs 默认 log(so_len 是 vm so, 会被忽略); so 由 browser 采集
                tok, _ = _rk_sentinel(session, session.device_id, FLOW_OAUTH, cfg, with_so=False)
                holder["tok2"] = tok
                logger.info("  [quickjs/t] create 真 t 就绪 t_len=%s (%.1fs, so 由 browser 采集)", len(tok), time.time() - _ct)
            except Exception as exc:
                holder["t_err"] = f"{type(exc).__name__}: {exc}"
            holder["t_s"] = time.time() - _ct

        def _gen_so() -> None:
            _ct = time.time()
            so = None
            # browser(默认): 无 so 必死(测活实证), 采集失败重试 3 次, 仍失败主线程中止
            # so-only 采集: 若配置 sentinel_so_page(frame.html 直连) 则用之省渲染;
            # 空则用 sentinel_browser_page(about-you, 默认)。
            so_page = str(_proto.get("sentinel_so_page") or "").strip()
            so_attempts = 0
            for _try in range(3):
                so_attempts = _try + 1
                try:
                    br = harvest_browser_sentinel(
                        cfg, flow=FLOW_OAUTH, device_id=session.device_id,
                        proxy=proxy_url, headless=True, timeout_s=90,
                        page_url=so_page or None,
                    )
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

        # 子线程复制调用线程 context: 批量并发用 contextvars 存账号归属时, so/t 采集子线程
        # 可继承(threading.local/threading.Thread 默认都不传播——否则 so 日志无账号前缀)。
        # 两点关键(实测踩坑):
        #  1) copy_context() 须在父线程求值(捕获含账号的 context 快照)——写在 lambda 里会在
        #     子线程执行时才求值, 捕获空 context(无前缀);
        #  2) 每个线程必须独立 context 对象——共享同一 Context 被多线程并发 run 会抛
        #     "cannot enter context ... already entered"(Context.run 单线程独占, so/t 双线程全挂)。
        _ctx_t = contextvars.copy_context()
        _ctx_so = contextvars.copy_context()
        _th_t = threading.Thread(target=lambda: _ctx_t.run(_gen_t))
        _th_so = threading.Thread(target=lambda: _ctx_so.run(_gen_so))
        _th_t.start()
        _th_so.start()
        # join 必须加超时: browser so 采集线程(playwright/Chrome) 偶发 hang(Chrome 无响应)时,
        # 无超时 join 无限等待 → 整个批量卡死(实测 5 线程 20 账号卡在 so 采集 join, 主线程
        # f.result 永久等待, 批量统计不打印、进程不退出)。超时后按 holder 无结果走失败分支。
        _th_t.join(timeout=90)   # quickjs t 一般 <5s
        _th_so.join(timeout=120)  # so 采集 ~10s + 重试 3 次
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
    if not so_b and holder.get("so_source") != "none":
        warn = holder.get("so_warn") or ""
        raise _SoFailed(f"so 采集失败({so_source}): {str(warn)[:120]}")

    _http_t0 = time.time()
    h2 = session.auth_api_headers(referer=ABOUT_YOU_REFERER)
    h2["openai-sentinel-token"] = tok2
    if so_b:
        h2["openai-sentinel-so-token"] = so_b
    # create 5xx 服务端临时错误重试(4xx 客户端错误不重试, 重试无意义)
    create_retries = max(1, int((cfg.get("register") or {}).get("create_retries", 3) or 3))
    create_retry_sleep = float((cfg.get("register") or {}).get("create_retry_sleep", 2.0) or 2.0)
    resp2 = None
    for _attempt in range(create_retries):
        resp2 = session.post(CREATE_URL, headers=h2, data=json.dumps({"name": name, "birthdate": bday}))
        if resp2.status_code == 200 or resp2.status_code < 500:
            break
        logger.warning("  [create] HTTP %s 服务端临时错误重试(%d/%d)", resp2.status_code, _attempt + 1, create_retries)
        time.sleep(create_retry_sleep)
    diag["create_http"] = resp2.status_code if resp2 is not None else 0
    diag["create_http_s"] = round(time.time() - _http_t0, 1)  # create HTTP 请求本身耗时
    diag["create_s"] = round(time.time() - _t0, 1)  # 纯 create 段(t+so 并行 + create HTTP)
    if resp2 is None or resp2.status_code != 200:
        raise _CreateFailed(f"create_account HTTP {resp2.status_code if resp2 else '?'}: {(resp2.text[:150]) if resp2 else ''}")
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
    proxy_pool=None,
) -> dict[str, Any]:
    """一次完整注册(signin→register→OTP→create→session), 返回注册凭据或抛分类异常。

    阶段序列编排器: 各阶段独立函数(Signin/Register/WaitOtp/Create/Session),
    统一 diag 累加, 任一段抛分类异常由 register_account 统一归类。
    """
    _setup_t0 = time.time()
    if proxy_pool is not None:
        # 池模式: 预建探活过的隧道直接取(免现场建隧道+探活 ~3-5s/号), 坏隧道池自愈
        resolved = proxy_pool.acquire()
        _from_pool = True
    else:
        resolved = resolve_proxy(cfg, override=proxy_url)
        _from_pool = False
    session = BrowserSession(cfg, proxy=resolved.session_url)
    st = {"start": time.time()}
    # setup_s: 隧道建立+会话初始化(在 st.start 之前, 故归入独立段, 不污染 signin 段)。
    # 隧道探活失败重建时偏大——IP 排查有价值(段外开销归因, 收窄"段和 vs 墙钟"差额)。
    diag: dict[str, Any] = {"setup_s": round(time.time() - _setup_t0, 1)}
    # ⭐ Geo 对齐(register-kit 借鉴): 出口 IP → 语言/时区, 防"时区/语言与出口地理位置
    # 对不上"被风控当设备指纹矛盾。会话语言 + 临时 cfg 时区(指纹用), 注册完恢复。
    _geo_saved: tuple | None = None
    try:
        # 隧道探活失败时跳过 Geo: 坏隧道查 ipwho.is 必失败(半开黑洞可等 ~150s),
        # 直接回退默认画像; 注册链首请求会因隧道坏而失败换 sid 重试, 新隧道再正常 Geo。
        if getattr(resolved, "probe_ok", True):
            from gptreg.proxyutil import geo_profile_for_proxy

            _geo = geo_profile_for_proxy(resolved.session_url or "", ipinfo=resolved.ipinfo)
            if _geo:
                session.accept_language = _geo["languages"]
                _b = cfg.setdefault("browser", {})
                _geo_saved = (_b.get("timezone"), _b.get("language"), _b.get("languages"))
                _b["timezone"] = _geo["timezone"]
                _b["language"] = _geo["language"]
                _b["languages"] = _geo["languages"]
                diag["geo"] = f"{_geo['country']}/{_geo['timezone']}"
        else:
            diag["geo"] = "skip(probe_failed)"
    except Exception:
        pass
    try:
        final = _stage_signin(session, email, diag)
        reg, send_url = _stage_register(session, cfg, account, email, password, final, st, diag)
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
            "so_len": len(so_b) if so_b else 0,
            "has_so": bool(so_b),
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
        if _from_pool:
            # 链中任一段异常: 隧道可能坏(网络)或业务失败——统一丢弃换新隧道
            # (池自动补建保持大小, 新 IP 对下个号有利), 不归还污染池
            try:
                proxy_pool.discard(resolved)
            except Exception:
                pass
        else:
            resolved.close()
        raise
    finally:
        # 恢复 cfg.browser(geo 临时覆盖), 避免影响其他账号/worker
        if _geo_saved is not None:
            _b = cfg.get("browser") or {}
            _b["timezone"], _b["language"], _b["languages"] = _geo_saved


def _enroll_recovery_now(session: BrowserSession, at: str, device_id: str, timeout: int = 30) -> dict:
    """开 recovery key(防 TOTP 锁死): enroll recovery_code → activate(提交整个 key)。

    2026-08-12 研究确认的纯协议流程(register_otp 已验证):
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
        resp = session.post(ENROLL_URL, headers=h, data=json.dumps({"factor_type": "recovery_code"}), timeout=timeout)
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
        resp2 = session.post(ACTIVATE_URL, headers=h, data=json.dumps({
            "code": key, "session_id": session_id,
            "factor_id": factor_id, "factor_type": "recovery_code"}), timeout=timeout)
        ok = resp2.status_code == 200 and '"success":true' in (resp2.text or "")
        logger.info("[TOTP/recovery] activate HTTP %s ok=%s", resp2.status_code, ok)
        return {"recovery_key": key if ok else "", "recovery_enrolled": ok}
    except Exception as exc:
        logger.warning("[TOTP/recovery] enroll 异常: %s", str(exc)[:100])
        return {"recovery_key": "", "recovery_enrolled": False}


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
            if not activated:
                # activate 未明确 success 时, 用 mfa_info 兜底确认(正常路径省 1 个请求 ~0.5-1s)
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
        # 同步开 recovery key(防 TOTP 锁死; 需 fresh token, 刚激活 TOTP 满足)。
        # enable_recovery=false 时跳过(省 2 个 HTTP 请求 ~1-2s; 代价=失去 TOTP 锁死兜底)。
        recovery = {"recovery_key": "", "recovery_enrolled": False}
        if (cfg.get("register") or {}).get("enable_recovery", True):
            recovery = _enroll_recovery_now(session, reg["at"], reg["device_id"])
        else:
            logger.info("[TOTP/recovery] enable_recovery=false, 跳过 recovery key")
        return {
            "totp_secret": enroll_secret,
            "totp_enrolled": True,
            "recovery_key": recovery.get("recovery_key") or "",
            "recovery_enrolled": bool(recovery.get("recovery_enrolled")),
        }
    except _EnrollFailed:
        raise
    except Exception as exc:
        # 网络/瞬时异常(curl 超时/断流等) → 归入 ENROLL_FAILED(账号已建, 2FA 可后补),
        # 不再逃逸导致整个批量中断(实测 activate_enrollment 超时崩溃线程池, dulcet 未落盘)
        raise _EnrollFailed(f"enroll 网络异常: {type(exc).__name__}: {str(exc)[:150]}") from exc
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
    proxy_pool=None,
) -> RegistrationResult:
    """执行一次密码注册 + TOTP 2FA, 返回结构化结果。

    proxy 空则走 config 动态模板(可换 sid 重试)。register 400: 落 log-in=邮箱已注册、
    invalid_auth_step/Invalid authorization=邮箱状态冲突(状态机不可重入) → 永久弃用;
    仅纯 IP 信誉才换 sid 重试 1 次(反复戳同一邮箱会放大认证请求量, 触发 rate_limit)。
    proxy_pool: ProxyPool 时从池 acquire 隧道(预建复用, 免每号现场建隧道),
                坏隧道池自愈(discard 换新), 成功 release 回池; None 走 resolve_proxy 兼容旧路径。
    """
    t0 = time.time()
    _from_pool = proxy_pool is not None
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
            reg, session, resolved = _register_chain(
                cfg, account, email, password, name, bday, proxy_url, proxy_pool=proxy_pool)
            break
        except _MailRegistered as exc:
            return RegistrationResult(
                RegisterOutcome.MAIL_REGISTERED, email,
                getattr(exc, "diag", None) or {"reason": str(exc)[:150]},
            )
        except _MailStateConflict as exc:
            # 邮箱状态冲突(已推进过注册): 换 IP 无效, 直接弃用, 不重试
            return RegistrationResult(
                RegisterOutcome.MAIL_CONFLICT, email,
                getattr(exc, "diag", None) or {"reason": str(exc)[:150]},
            )
        except _RegisterBlocked as exc:
            last_outcome = RegisterOutcome.IP_BLOCKED
            last_diag = dict(getattr(exc, "diag", {}) or {})
            last_diag["attempt"] = att + 1
            logger.warning("  [warn] register 被拒(IP 风控): %s", str(exc)[:70])
            # 邮箱级风控(email-verification 新流程): 同邮箱换 IP 无效(实测换 3-5 IP 全失败,
            # 该邮箱/域名已被 OpenAI 要求走邮箱验证) → 直接失败让批量换新号;
            # 号保留(batch 不弃用), 下次批量可再试。
            if "email-verification" in str(last_diag.get("landing_diag") or ""):
                last_diag["email_verification_required"] = True
                return RegistrationResult(RegisterOutcome.IP_BLOCKED, email, last_diag)
            # 仅换 IP 重试 1 次(att=0 → 第二次尝试): 反复戳同一邮箱会放大认证请求量
            if not auto_retry or att >= 1:
                break
            if _from_pool:
                # 池模式: 隧道已在 _register_chain 异常路径 discard, 下一轮 acquire 拿新隧道(新 IP)
                last_diag["retry_sid"] = att + 1
                logger.warning("  [retry] 池换隧道重试 (%d/2)", att + 1)
                time.sleep(1)
                continue
            if "-sid-" not in (proxy_url or "") or "-t-" not in (proxy_url or ""):
                break
            new_sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            proxy_url = re.sub(r"-sid-[^-]+-t-", f"-sid-{new_sid}-t-", proxy_url, count=1)
            last_diag["retry_sid"] = att + 1
            logger.warning("  [retry] 换新 sid 重试 (%d/2)", att + 1)
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
            if _from_pool:
                continue  # 池模式: 隧道已 discard, 下一轮 acquire 换新隧道
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
            if _from_pool:
                proxy_pool.release(resolved)  # 成功/账号已建: 隧道没问题, 归还复用
            else:
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
    rec["recovery_key"] = totp.get("recovery_key") or ""   # 30字符 key, 防 TOTP 锁死(恢复因子)
    rec["sentinel_obs"] = {
        "challenge_mode": "quickjs_pwd_v3",
        "create_has_so": reg["has_so"],
        "create_so_len": reg["so_len"],
        "t_len": reg["t_len"],
        "flow": FLOW_PWD,
        "create_flow": FLOW_OAUTH,
        "totp_enrolled": True,
        "recovery_enrolled": bool(totp.get("recovery_enrolled")),
        "health_s": health_s,   # 秒封检测耗时(段增量), 便于 2FA 激活/存活耗时分析
        "enroll_s": enroll_s,   # 2FA enroll→activate 耗时(段增量)
    }
    return rec

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码注册 V3 完整链(纯 HTTP,学自 codex-register):
  homepage → csrf → signin → authorize → register(设密码,别名) → send_otp
  → validate_otp → create_account → callback → token

register 用 username_password_create flow(无 so);create_account 先用 oauth_create_account flow,
暂不加 so(codex-register 做法),观察服务端是否接受。

重试: 基建失败(TLS/SSL)自动换 IP 重试一次(每次重新 resolve_proxy → 新 sid)。
耗时: 分阶段(signin_register/otp_wait/validate_create/total)成功落盘 sentinel_obs.timing_s,失败打日志。

用法: python capture/verify_pwd_v3.py [--email 主号] [--alias]
"""
from __future__ import annotations

import json
import random
import string
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台 GBK 无法打印 emoji(✅ 等),强制 UTF-8 输出,否则 print 抛 UnicodeEncodeError 掩盖真实结果
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config, resolve_path  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.sentinel_quickjs import get_sentinel_token_via_quickjs  # noqa: E402
from gptreg.mail.providers import MailClientError, build_mail_client, mail_identity_key, UsedCodeCache  # noqa: E402
from gptreg.pipeline import _root  # noqa: E402

FLOW_PWD = "username_password_create"
FLOW_OAUTH = "oauth_create_account"
REGISTER_URL = "https://auth.openai.com/api/accounts/user/register"
PASSWORD_REFERER = "https://auth.openai.com/create-account/password"
ABOUT_YOU_REFERER = "https://auth.openai.com/about-you"

# 判定是否为基建失败(TLS/SSL),可换 IP 重试
_TLS_MARKERS = (
    "curl: (35)", "curl: (7)", "sslerror", "openssl", "tls connect",
    "tls ", "socketerror", "proxyerror", "connection reset",
)


def _is_tls_like(err: str) -> bool:
    e = (err or "").lower()
    return any(m in e for m in _TLS_MARKERS)


def random_password(length: int = 14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choice(chars) for _ in range(length))


def make_alias(email: str) -> str:
    name, dom = email.split("@")
    tag = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"{name}+{tag}@{dom}"


def _base(m: str) -> str:
    """取主号：x+tag@dom → x@dom。入参可带 tag/域，或裸用户名(无 @)原样返回。"""
    parts = (m or "").split("@")
    if len(parts) < 2:
        return parts[0].split("+")[0]
    return parts[0].split("+")[0] + "@" + parts[1]


def _run_once(
    cfg: dict,
    args,
    *,
    account: dict,
    email: str,
    password: str,
    display_name: str,
    bday: str,
    base_email: str,
    t0: float,
    tag: str,
) -> tuple[int, str | None]:
    """执行一次完整注册(独立 resolve_proxy→换 IP)。返回 (exit_code, err_str|None)。"""
    resolved = resolve_proxy(cfg, override=args.proxy)
    session = BrowserSession(cfg, proxy=resolved.session_url)
    print(f"[{tag}] 代理: {resolved.label()}")

    # 分阶段计时(统一墙钟 time.time(), 与 elapsed/t0 一致；勿混用 monotonic)
    st: dict[str, float] = {"start": time.time()}

    def mark(name: str) -> None:
        st[name] = time.time()
        st["last"] = st[name]

    try:
        # 1. signin → authorize
        auth.get_providers(session)
        time.sleep(0.3)
        csrf = auth.get_csrf_token(session)
        time.sleep(0.3)
        au = auth.signin_openai(session, csrf, email)
        time.sleep(0.3)
        final = auth.follow_authorize(session, au, attempts=1)
        print(f"authorize 落点: {final[:70]}")
        time.sleep(0.5)
        mark("authorize_done")

        # 2. register(设密码)
        print("\n[1] register(设置密码)...")
        token, so_header = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_PWD, cfg=cfg)
        headers = session.auth_api_headers(referer=PASSWORD_REFERER)
        headers["openai-sentinel-token"] = token
        resp = session.post(REGISTER_URL, headers=headers, data=json.dumps({"username": email, "password": password}))
        print(f"  HTTP {resp.status_code}: {(resp.text or '')[:150]}")
        if resp.status_code != 200:
            print("  [x] register 失败,中止")
            return 2, f"register http {resp.status_code}"
        reg = resp.json()
        mark("register_done")

        # 3. send_otp
        print("\n[2] send_otp(发验证码)...")
        send_url = reg.get("continue_url") or "https://auth.openai.com/api/accounts/email-otp/send"
        r = session.get(send_url, headers=session.auth_navigate_headers(referer=PASSWORD_REFERER), allow_redirects=True)
        print(f"  HTTP {r.status_code} 落点: {str(getattr(r, 'url', ''))[:70]}")
        otp_after = time.time()
        mark("otp_wait_start")

        # 4. 收码（IMAP 秒级到件；短超时 + 超时自动重发码，最多 otp_max_attempts 次）
        print("\n[3] 收 OTP...")
        client = build_mail_client(account, proxy=resolved.session_url or None,
                                   impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"))
        identity = mail_identity_key(account)
        cache_path = resolve_path(cfg.get("mail", {}).get("used_code_cache", "data/used_otp_codes.json"), _root(cfg))
        used_cache = UsedCodeCache(cache_path)
        exclude = used_cache.seen_codes(identity)
        mail_cfg = cfg.get("mail", {})
        otp_timeout = int(mail_cfg.get("otp_wait", 45) or 45)
        otp_max_attempts = max(1, int(mail_cfg.get("otp_max_attempts", 2) or 2))
        send_url = reg.get("continue_url") or "https://auth.openai.com/api/accounts/email-otp/send"
        otp = None
        for attempt in range(otp_max_attempts):
            try:
                otp = client.wait_for_otp(
                    # after_ts 用流程开始时刻(signin前)：邮件可能在 authorize 时就已到
                    # (2026-08-06 实测 send_otp 前邮件已到,otp_after 会晚于邮件→全过滤)。
                    after_ts=st["start"],
                    timeout=otp_timeout,
                    interval=3, settle_seconds=5,
                    exclude_codes=exclude,
                )
                break
            except Exception as exc:
                if attempt >= otp_max_attempts - 1:
                    raise
                print(f"  [OTP] 第{attempt+1}次收码失败({type(exc).__name__}: {str(exc)[:60]})，重发验证码...")
                time.sleep(1)
                r_retry = session.get(
                    send_url,
                    headers=session.auth_navigate_headers(referer=PASSWORD_REFERER),
                    allow_redirects=True,
                )
                print(f"  重发: HTTP {r_retry.status_code} 落点={str(getattr(r_retry, 'url', ''))[:50]}")
                otp_after = time.time()
        mark("otp_got")
        used_cache.remember(identity, otp, email=email, status="submitted")
        print(f"  OTP: {otp}")

        # 5. validate_otp(不需要 sentinel,codex 做法)
        print("\n[4] validate_otp...")
        vr = auth.validate_email_otp(session, otp, None)
        print(f"  {str(vr)[:200]}")

        # 6. create_account(vm t + browser so)——密码模式 + 真 so 的存活验证
        print("\n[5] create_account(vm t + browser so)...")
        tok2, _so2 = get_sentinel_token_via_quickjs(session, session.device_id, flow=FLOW_OAUTH, cfg=cfg)
        # browser 采真 so(cliproxy;browser 独立 req 拿 challenge,与 vm t 的 c 可能不同——混合模式已验证可接受)
        so_b = None
        try:
            from gptreg.browser_sentinel import harvest_browser_sentinel

            br = harvest_browser_sentinel(
                cfg, flow=FLOW_OAUTH, device_id=session.device_id,
                proxy=resolved.session_url, headless=True, timeout_s=90,
            )
            if br.get("ok") and br.get("so_header"):
                so_b = br["so_header"]
                print(f"  browser so: len={len(so_b)}")
            else:
                print(f"  browser so 采集失败: {br.get('error')}")
        except Exception as exc:
            print(f"  browser so 异常: {type(exc).__name__}: {str(exc)[:80]}")
        h2 = session.auth_api_headers(referer=ABOUT_YOU_REFERER)
        h2["openai-sentinel-token"] = tok2
        if so_b:
            h2["openai-sentinel-so-token"] = so_b
            print("  已带 openai-sentinel-so-token 头")
        resp2 = session.post("https://auth.openai.com/api/accounts/create_account",
                             headers=h2, data=json.dumps({"name": display_name, "birthdate": bday}))
        print(f"  HTTP {resp2.status_code}: {(resp2.text or '')[:200]}")
        mark("create_done")

        # register 已创建密码账号(200),create_account 补资料冲突(400 user_already_exists)
        # 密码账号存在,可密码登录 → 用于 2FA。保存 email+password。
        if resp2.status_code == 400 and "user_already_exists" in (resp2.text or ""):
            timing_pwd = {
                "total_s": round(st["create_done"] - st["start"], 1),
                "signin_register_s": round(st.get("register_done", st["start"]) - st["start"], 1),
                "otp_wait_s": round(st["otp_got"] - st.get("otp_wait_start", st["authorize_done"]), 1),
                "validate_create_s": round(st["create_done"] - st["otp_got"], 1),
            }
            pwd_rec = {
                "email": email,
                "password": password,
                "name": display_name,
                "birthdate": bday,
                "mail_main": base_email,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "note": "密码账号已创建(register 200),create_account 400 user_already_exists",
                "timing_s": timing_pwd,
            }
            pwd_file = ROOT / "data" / "pwd_accounts.jsonl"
            pwd_file.parent.mkdir(parents=True, exist_ok=True)
            with pwd_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(pwd_rec, ensure_ascii=False) + "\n")
            print(f"  ✅ 密码账号已创建并保存: {email} 密码={password} (可密码登录) 耗时={timing_pwd['total_s']}s")
            return 0, None

        # 7. callback + session
        if resp2.status_code == 200:
            cr = resp2.json()
            cu = cr.get("continue_url") or cr.get("url")
            if cu:
                print(f"\n[6] OAuth callback: {cu[:80]}")
                auth.follow_oauth_callback(session, cu)
                info = auth.fetch_session(session)
                at = info.get("accessToken")
                print(f"  access_token: {str(at)[:30]}...")
                health = auth.check_account_health(session, at)
                hs = health.get("status")
                print(f"  健康: {hs} {(str(health.get('body') or health.get('detail')) or '')[:80]}")
                mark("health_done")

                timing = {
                    "total_s": round(st["health_done"] - st["start"], 1),
                    "signin_register_s": round(st.get("register_done", st["start"]) - st["start"], 1),
                    "otp_wait_s": round(st["otp_got"] - st.get("otp_wait_start", st["authorize_done"]), 1),
                    "validate_create_s": round(st["create_done"] - st["otp_got"], 1),
                    "finalize_health_s": round(st["health_done"] - st["create_done"], 1),
                }

                # 保存账号(邮箱/密码/token/cookies)
                try:
                    cookies = [
                        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                         "secure": bool(getattr(c, "secure", False)), "expires": getattr(c, "expires", None)}
                        for c in session.session.cookies.jar
                    ]
                    record = {
                        "email": email,
                        "password": password,
                        "access_token": at,
                        "device_id": session.device_id,
                        "name": display_name,
                        "birthdate": bday,
                        "mail_main": base_email,
                        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "sentinel_obs": {
                            "challenge_mode": "quickjs_pwd_v3",
                            # register(设密码)阶段:username_password_create flow 无 so
                            "has_so": bool(so_b),
                            "so_len": len(so_b or ""),
                            "t_len": len(token),
                            "flow": FLOW_PWD,
                            "create_flow": FLOW_OAUTH,
                            # create_account 阶段:browser 真 so
                            "create_has_so": bool(so_b),
                            "create_so_len": len(so_b or ""),
                            # 注册耗时(秒, wall 时钟对照用)
                            "duration_s": round(time.time() - t0, 1),
                            "dur_signin_register_s": round(otp_after - t0, 1),
                            "dur_otp_wait_s": round(st["otp_got"] - st["authorize_done"], 1),
                            "dur_validate_create_s": round(st["create_done"] - st["otp_got"], 1),
                            # 分阶段计时(monotonic, 精确)
                            "timing_s": timing,
                            "retry_tag": tag,
                        },
                        "session_cookies": cookies,
                        "health": hs,
                    }
                    acc_file = ROOT / "output" / "accounts.jsonl"
                    acc_file.parent.mkdir(parents=True, exist_ok=True)
                    with acc_file.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    print(f"  ✅ 已保存: {email} 密码={password} 耗时={timing['total_s']}s")
                except Exception as exc:
                    print(f"  [warn] 保存失败: {exc}")
            return 0, None
        return 3, f"create_account http {resp2.status_code}"

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        stage = st.get("last") and max(
            (k for k in ("authorize_done", "register_done", "otp_wait_start", "otp_got", "create_done", "health_done") if k in st),
            key=lambda k: st[k],
            default=None,
        )
        # 真实经过时间(墙钟)；st["last"] 在长阻塞(如 OTP 等待)期间不更新，勿用它算耗时
        elapsed = round(time.time() - st["start"], 1)
        print(f"  [x] [{tag}] 失败 stage={stage} elapsed={elapsed}s: {err}")
        return 4, err
    finally:
        resolved.close()


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--alias", action="store_true")
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    args = ap.parse_args()

    cfg = load_config()

    # 号池账号(用于收码)
    pool_lines = [l.strip() for l in Path("mail_pool.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    account = None
    target_main = args.email or ""
    from gptreg.mail.pool import parse_mail_line
    for line in pool_lines:
        a = parse_mail_line(line)
        if not a:
            continue
        if target_main and _base(a["email"]) != _base(target_main):
            continue
        account = a
        break
    if not account:
        print("号池找不到收码账号")
        return 1
    base_email = account["email"]
    email = make_alias(base_email) if args.alias else base_email
    password = random_password()
    display_name = "James Miller"
    bday = "1998-05-12"
    print(f"注册邮箱: {email}  密码: {password}  收码主号: {base_email}")
    t0 = time.time()  # 注册总耗时起点

    # 首试 + 基建失败(TLS/SSL)换 IP 重试一次
    for attempt in (1, 2):
        tag = "首试" if attempt == 1 else "重试"
        code, err = _run_once(
            cfg, args,
            account=account, email=email, password=password,
            display_name=display_name, bday=bday, base_email=base_email,
            t0=t0, tag=tag,
        )
        if code == 0:
            return 0
        # 仅 TLS/SSL 基建失败才重试(换 IP);其他失败(register 拒/OTP 失败/create 400)不重试
        if attempt == 1 and err and _is_tls_like(err):
            print(f"\n[重试] 基建失败({err[:60]}) → 换 IP 重试一次")
            continue
        return code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

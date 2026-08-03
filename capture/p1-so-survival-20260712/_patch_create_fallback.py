"""One-shot patch: create retries + optional browser fallback + mailbox note."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# --- config defaults ---
cfg_path = ROOT / "gptreg" / "config.py"
cfg = cfg_path.read_text(encoding="utf-8")
old = '''    "register": {
        "default_name": "",
        "birthday_year_min": 1995,
        "birthday_year_max": 2005,
        "finalize_attempts": 5,
        # Step B：登录后 me + conversation/init + prepare（不 finalize/不造假）
        "post_login": False,
    },'''
new = '''    "register": {
        "default_name": "",
        "birthday_year_min": 1995,
        "birthday_year_max": 2005,
        "finalize_attempts": 5,
        # Step B：登录后 me + conversation/init + prepare（不 finalize/不造假）
        "post_login": False,
        # create 400 同 body 重试（对齐资料 zip，不改 sentinel）
        "create_retries": 3,
        "create_retry_sleep": 2.0,
        # pow 路径 create 遇 registration_disallowed 后，同会话当次 browser 再试 1 次（opt-in，默认关）
        "create_browser_fallback": False,
    },'''
if old not in cfg:
    raise SystemExit("config block not found")
cfg_path.write_text(cfg.replace(old, new, 1), encoding="utf-8")
print("config OK")

pipe_path = ROOT / "gptreg" / "pipeline.py"
pipe = pipe_path.read_text(encoding="utf-8")

old_create = '''        # create_account 阶段 sentinel（pow 默认；browser opt-in 见 protocol.sentinel_source）
        sentinel_create, so_header = auth.make_sentinel_headers(
            session, None, "oauth_create_account", require_so=False
        )
        has_so = bool(so_header)
        so_len = len(so_header or "")
        sentinel_meta = getattr(session, "_last_sentinel_meta", None) or {}
        challenge_mode = str(sentinel_meta.get("mode") or (cfg.get("protocol") or {}).get("sentinel_source") or "pow")
        logger.info(
            "[Sentinel/obs] create flow=oauth_create_account mode=%s has_so=%s so_len=%s t_len=%s",
            challenge_mode,
            has_so,
            so_len,
            sentinel_meta.get("t_len"),
        )
        time.sleep(0.2)
        create_result = auth.create_account(
            session,
            display_name,
            bday,
            sentinel_create,
            so_header,
            require_so=False,
        )
        create_acked = True
        continue_url = create_result.get("continue_url")
        if not continue_url:
            raise RuntimeError(f"create_account 无 continue_url: {create_result}")
'''

new_create = '''        # create_account：pow 默认；可选同 body 重试 + disallow 后当次 browser 回退
        reg_cfg = cfg.get("register") or {}
        create_retries = max(1, int(reg_cfg.get("create_retries", 3) or 3))
        create_retry_sleep = float(reg_cfg.get("create_retry_sleep", 2.0) or 2.0)
        browser_fallback = bool(reg_cfg.get("create_browser_fallback", False))
        base_source = str((cfg.get("protocol") or {}).get("sentinel_source") or "pow").strip().lower()
        if base_source in {"browser", "pw", "playwright", "chrome"}:
            base_source = "browser"
        else:
            base_source = "pow"

        create_result = None
        create_last_err: Exception | None = None
        has_so = False
        so_len = 0
        challenge_mode = base_source
        sentinel_meta: dict[str, Any] = {}
        create_attempts_log: list[dict[str, Any]] = []

        def _one_create_wave(source: str) -> dict[str, Any]:
            nonlocal has_so, so_len, challenge_mode, sentinel_meta, create_last_err
            sentinel_create, so_header = auth.make_sentinel_headers(
                session, None, "oauth_create_account", require_so=False, source=source
            )
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
            for attempt in range(create_retries):
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
                    if is_disallow and attempt < create_retries - 1:
                        # 资料 zip：同 body 重试；不换 name/邮箱、不改 sentinel 内容
                        logger.warning(
                            "[Auth] registration_disallowed，同 body 重试 (%s/%s) mode=%s",
                            attempt + 1,
                            create_retries,
                            source,
                        )
                        continue
                    break
            create_last_err = last_exc
            if last_exc:
                raise last_exc
            raise RuntimeError("create_account failed without exception")

        try:
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
                    "[Auth] pow create disallow 后当次 browser 回退（create_browser_fallback=true）"
                )
                create_result = _one_create_wave("browser")
            else:
                raise

        create_acked = True
        continue_url = (create_result or {}).get("continue_url")
        if not continue_url:
            raise RuntimeError(f"create_account 无 continue_url: {create_result}")
'''

if old_create not in pipe:
    raise SystemExit("pipeline create block not found")
pipe = pipe.replace(old_create, new_create, 1)

old_obs = '''        sentinel_obs = {
            "flow": "oauth_create_account",
            "challenge_mode": challenge_mode,
            "has_so": has_so,
            "so_len": so_len,
            "t_len": sentinel_meta.get("t_len"),
            "browser_elapsed_s": sentinel_meta.get("elapsed_s"),
            "sdk_keys": sentinel_meta.get("sdk_keys"),
            "chatreq": chatreq_obs,
            "post_login": post_login_enabled,
            "post_login_ok": bool((post_login_detail or {}).get("ok")) if post_login_enabled else None,
            "post_login_detail": post_login_detail,
        }
'''
new_obs = '''        sentinel_obs = {
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
        }
'''
if old_obs not in pipe:
    raise SystemExit("sentinel_obs block not found")
pipe = pipe.replace(old_obs, new_obs, 1)

old_mark = '''            if result.get("success"):
                result.setdefault("fail_bucket", "success")
                mail_pool.mark_used(email)
            else:
                result["fail_bucket"] = classify_result(result)
                if result.get("create_acknowledged"):
                    # 远端可能已消耗邮箱
                    mail_pool.mark_bad(email, reason=result.get("error", ""))
                else:
                    mail_pool.mark_failed(email)
'''
new_mark = '''            if result.get("success"):
                result.setdefault("fail_bucket", "success")
                mail_pool.mark_used(email)
            else:
                result["fail_bucket"] = classify_result(result)
                bucket = result["fail_bucket"]
                if result.get("create_acknowledged"):
                    # create 已 200 但后续失败：邮箱侧可能已占用
                    mail_pool.mark_bad(email, reason=result.get("error", ""))
                elif bucket == "create_disallow":
                    # OpenAI 拒建号 ≠ 邮箱封死；OTP 往往仍通。记 fail 进 retrying，勿 mark_bad
                    mail_pool.mark_failed(email)
                    result["mailbox_note"] = "create_disallow_not_mailbox_ban"
                else:
                    mail_pool.mark_failed(email)
'''
if old_mark not in pipe:
    raise SystemExit("mark block not found")
pipe = pipe.replace(old_mark, new_mark, 1)
pipe_path.write_text(pipe, encoding="utf-8")
print("pipeline OK")

yp = ROOT / "config.yaml"
if yp.exists():
    yt = yp.read_text(encoding="utf-8")
    if "create_browser_fallback" not in yt:
        lines = yt.splitlines(True)
        out: list[str] = []
        inserted = False
        for ln in lines:
            out.append(ln)
            if (not inserted) and ln.strip().startswith("post_login:"):
                indent = ln[: len(ln) - len(ln.lstrip())]
                out.append(f"{indent}# create 400 同 body 重试次数（资料 zip 对齐）\n")
                out.append(f"{indent}create_retries: 3\n")
                out.append(f"{indent}create_retry_sleep: 2.0\n")
                out.append(f"{indent}# pow 遇 registration_disallowed 后同会话当次 browser 1 次（默认关）\n")
                out.append(f"{indent}create_browser_fallback: false\n")
                inserted = True
        if inserted:
            yp.write_text("".join(out), encoding="utf-8")
            print("config.yaml OK")
        else:
            print("config.yaml skip")
    else:
        print("config.yaml already")
print("done")

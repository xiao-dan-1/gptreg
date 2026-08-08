#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码注册 UI 逆向 + 自动循环试号。

正确流程(已逆向):validate OTP → GET create-account/password(页面加载推进 auth step)
→ 填密码 → POST /api/accounts/user/register。

卡点:username(邮箱)必须全局唯一,号池大量主邮箱已在 OpenAI 注册。
本脚本自动循环试号:register 返回 username_already_exists / session ended 就换下一个邮箱,
直到 register 成功(HTTP 200)。

用法:
    python capture/playwright_password_register.py                # 自动试号,最多 8 个
    python capture/playwright_password_register.py --limit 3
    python capture/playwright_password_register.py --email x@...  # 单邮箱
    python capture/playwright_password_register.py --headful      # 观察浏览器
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gptreg.config import load_config  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402
from gptreg.mail.pool import parse_mail_line  # noqa: E402
from gptreg.mail.providers import build_mail_client  # noqa: E402
from capture.verify_password_register import random_password, _base  # noqa: E402


def _candidates(cfg: dict, limit: int, force_email: str) -> list[dict]:
    """候选号:过滤 accounts.jsonl + 号池 state 已用主号。"""
    taken: set[str] = set()
    st_path = ROOT / "mail_pool.txt.state.json"
    if st_path.exists():
        try:
            for u in json.loads(st_path.read_text(encoding="utf-8")).get("used") or []:
                if isinstance(u, str):
                    taken.add(_base(u).lower())
        except Exception:
            pass
    acc_path = ROOT / "output" / "accounts.jsonl"
    if acc_path.exists():
        for line in acc_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("email"):
                    taken.add(_base(d["email"]).lower())
            except Exception:
                pass
    pool_file = str((cfg.get("mail") or {}).get("pool_file") or "mail_pool.txt")
    out = []
    for line in Path(pool_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        a = parse_mail_line(line)
        if not a:
            continue
        m = a["email"]
        if force_email:
            if _base(m).lower() == _base(force_email).lower():
                return [a]
            continue
        if _base(m).lower() in taken:
            continue
        out.append(a)
        if len(out) >= limit:
            break
    return out


def _run_one(
    cfg: dict, resolved, account: dict, email: str, password: str,
    headless: bool, proxy_url: str, on_response: dict,
) -> dict:
    """单邮箱完整密码注册,返回分类结果。"""
    from playwright.sync_api import sync_playwright

    client = build_mail_client(
        account,
        proxy=proxy_url or None,
        impersonate=cfg.get("browser", {}).get("impersonate", "chrome142"),
    )
    since = time.time()
    result: dict = {"email": email, "stage": "start"}
    last_reg: dict = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            proxy={"server": proxy_url},
        )
        ctx = browser.new_context(
            user_agent=cfg.get("browser", {}).get("user_agent"),
            locale="en-US", viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()

        def on_req(req):
            u = req.url
            if "openai.com" not in u or "awe/api" in u or "cdn-cgi" in u or "font" in u \
                    or "sdk.js" in u or "frame.html" in u:
                return
            if req.method == "POST":
                print(f"    [{req.method}] {u[-70:]}")

        def on_res(resp):
            u = resp.url
            if "user/register" in u:
                try:
                    body = resp.text()[:300]
                except Exception:
                    body = ""
                last_reg["status"] = resp.status
                last_reg["body"] = body
                print(f"    [RESP register] {resp.status}: {body[:160]}")
                if resp.status == 200:
                    result["stage"] = "register_ok"
                    result["register_body"] = body
            elif "email-otp/validate" in u:
                result["stage"] = "validate_ok"
                try:
                    body = resp.text()[:800]
                    result["validate_body"] = body
                    # OAuth code/登录流程 = 该邮箱已注册;about-you = 新用户可注册
                    if "callback/openai" in body or '"code"' in body or "external_url" in body:
                        result["validate_type"] = "login_already_registered"
                    else:
                        result["validate_type"] = "register_flow"
                    print(f"    [validate 类型] {result['validate_type']}")
                except Exception:
                    pass

        page.on("request", on_req)
        page.on("response", on_res)

        # 1. 登录页 → 邮箱
        page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        for name in ("Sign up", "Create account", "Get started"):
            try:
                b = page.get_by_role("button", name=name)
                if b.count():
                    b.first.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass
        filled = False
        for sel in ("input[name=email]", "input[type=email]", "#email-input"):
            try:
                el = page.locator(sel).first
                if el.count():
                    el.fill(email)
                    filled = True
                    break
            except Exception:
                pass
        if not filled:
            try:
                page.locator("input[type=text]").first.fill(email)
                filled = True
            except Exception:
                pass
        if not filled:
            result["stage"] = "no_email_input"
            ctx.close()
            browser.close()
            return result
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

        # 2. OTP
        otp = None
        try:
            otp = client.wait_for_otp(
                after_ts=since,
                timeout=max(int(cfg.get("mail", {}).get("max_wait", 90)), 150),
                interval=3, settle_seconds=5,
            )
            print(f"    OTP: {otp}")
        except Exception as exc:
            result["stage"] = "otp_fail"
            result["detail"] = str(exc)[:80]
            ctx.close()
            browser.close()
            return result
        filled = False
        for sel in ("input[name=otp]", "[autocomplete=one-time-code]", "input[type=tel]"):
            try:
                el = page.locator(sel).first
                if el.count():
                    el.fill(otp)
                    filled = True
                    break
            except Exception:
                pass
        if not filled:
            try:
                page.locator("input[type=text]").first.fill(otp)
                filled = True
            except Exception:
                pass
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(3500)
        result["stage"] = "validate_ok"
        if result.get("validate_type") == "login_already_registered":
            result["stage"] = "already_registered"
            print("    [跳过] 该邮箱已注册(登录流程),密码页 session ended 必然")
            ctx.close()
            browser.close()
            return result

        # 3. 导航密码页
        try:
            page.goto("https://auth.openai.com/create-account/password",
                      wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

        # 4. 等密码框
        pw_found = False
        for _ in range(15):
            pw = page.locator("input[type=password], input[name=password], "
                              "input[name=new-password]").first
            if pw.count():
                pw.fill(password)
                try:
                    page.keyboard.press("Enter")
                except Exception:
                    try:
                        page.locator("button[type=submit]").first.click()
                    except Exception:
                        pass
                pw_found = True
                break
            page.wait_for_timeout(1000)
        if not pw_found:
            try:
                title = page.title()
                if "session has ended" in title.lower():
                    result["stage"] = "session_ended"
                else:
                    result["stage"] = "no_password_input"
                    result["title"] = title[:80]
            except Exception:
                result["stage"] = "no_password_input"
            ctx.close()
            browser.close()
            return result

        # 5. 等 register 响应
        for _ in range(10):
            if result.get("stage") == "register_ok" or last_reg.get("status") is not None:
                break
            page.wait_for_timeout(1000)
        if last_reg.get("status") is not None:
            result["stage"] = "register_done"
            result["register_status"] = last_reg.get("status")
            result["register_body"] = last_reg.get("body", "")
            if last_reg.get("status") == 200:
                result["stage"] = "register_ok"
                try:
                    result["final_url"] = page.url[:150]
                except Exception:
                    pass
        else:
            result["stage"] = "no_register_response"
        ctx.close()
        browser.close()
    return result


def main() -> int:
    import argparse as _ap

    ap = _ap.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    resolved = resolve_proxy(cfg, override=args.proxy or None)
    proxy_url = resolved.session_url
    print(f"代理: {resolved.label()}")

    cands = _candidates(cfg, args.limit, args.email)
    if not cands:
        print("无候选邮箱")
        return 1
    print(f"候选 {len(cands)} 个")

    attempted = {}
    for i, account in enumerate(cands):
        email = account["email"]
        password = random_password()
        print(f"\n[{i + 1}/{len(cands)}] 尝试 {email} 密码={password}")
        r = _run_one(cfg, resolved, account, email, password,
                     headless=not args.headful, proxy_url=proxy_url,
                     on_response={})
        attempted[email] = r["stage"]
        print(f"    -> {r['stage']} {r.get('detail', '')}")
        if r["stage"] == "register_ok":
            print(f"\n🎉 密码注册成功! 邮箱={email} 密码={password}")
            print(f"    register_body: {r.get('register_body', '')[:200]}")
            print(f"    final_url: {r.get('final_url', '')}")
            return 0
        if r["stage"] in ("otp_fail",):
            print("    (OTP 失败,可能邮箱异常,继续)")
    resolved.close()
    print(f"\n=== 全部失败,结果分布 ===")
    for e, s in attempted.items():
        print(f"  {e}: {s}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

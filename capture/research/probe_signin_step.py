#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断: 不同邮箱 signin 后 authorize 落点差异。

批量失败: 所有密码注册 register 400 invalid_auth_step, authorize 落点 email-verification
(不是 register 需要的 password step)。本脚本对比「随机新邮箱」vs「号池未用过邮箱」的落点,
区分「号池邮箱被 OpenAI 标记」vs「注册流程整体变化」。

用法: python capture/probe_signin_step.py [--proxy 动态或固定]
"""
from __future__ import annotations

import random
import re
import string
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402
from gptreg import auth  # noqa: E402
from gptreg.proxyutil import resolve_proxy  # noqa: E402


def _dyn_proxy(cfg) -> str:
    tpl = str((cfg.get("proxy") or {}).get("dynamic", {}).get("template") or "")
    if not tpl:
        return "http://127.0.0.1:10808"
    sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return re.sub(r"-sid-[a-zA-Z0-9]+-t-", f"-sid-{sid}-t-", tpl)


def _probe(cfg, proxy_url: str, email: str) -> str:
    r = resolve_proxy(cfg, override=proxy_url)
    s = BrowserSession(cfg, proxy=r.session_url)
    try:
        auth.get_providers(s)
        time.sleep(0.3)
        csrf = auth.get_csrf_token(s)
        time.sleep(0.3)
        au = auth.signin_openai(s, csrf, email)
        time.sleep(0.3)
        final = auth.follow_authorize(s, au, attempts=1)
        # 落点 + 关键 cookie
        cookies = {c.name: (c.value or "")[:20] for c in s.session.cookies.jar}
        keys = {k for k in cookies if any(x in k for x in ("login_session", "oai-client-auth", "oai-sc"))}
        return f"落点={final[:75]}  cookies={sorted(keys)}"
    except Exception as exc:
        return f"异常 {type(exc).__name__}: {str(exc)[:80]}"
    finally:
        r.close()


def main() -> int:
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--proxy", default="")
    args = ap.parse_args()

    cfg = load_config()
    proxy_url = args.proxy or _dyn_proxy(cfg)
    print(f"代理: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")

    # 1. 随机全新邮箱(不在任何系统)
    rand_email = "zz_" + "".join(random.choices(string.ascii_lowercase, k=12)) + "@outlook.com"
    print(f"\n[1] 随机新邮箱: {rand_email}")
    print(f"    {_probe(cfg, proxy_url, rand_email)}")

    # 2. 号池未用过邮箱(之前注册失败的)
    for e in ("AdamAdams2659@outlook.com", "KirstenScott5455@outlook.com"):
        print(f"\n[2] 号池邮箱: {e}")
        print(f"    {_probe(cfg, proxy_url, e)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

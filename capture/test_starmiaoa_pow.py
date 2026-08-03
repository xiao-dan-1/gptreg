# -*- coding: utf-8 -*-
"""试验 starmiaoa 纯 Python PoW + 密码注册路线（零 Node 依赖）。

对比 starmiaoa/chatgpt-register-k12 (⭐171) 的实现：
- Sentinel: 纯 Python FNV-1a PoW → 直接打 sentinel.openai.com
- Token JSON: {p, t:"", c, id, flow}  ← t 为空，无 turnstile
- 无 so 处理
- 注册流程: platform OAuth authorize → authorize/continue → user/register(设密码) → OTP → validate → create_account → oauth/token

目标：验证纯 Python PoW 能否绕开 Node VM 的 so 局限，用 password 路线完成注册。
"""
from __future__ import annotations

import base64
import json
import random
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from curl_cffi.requests import Session

# ── 配置 ────────────────────────────────────────────────────
PROXY = "http://127.0.0.1:7890"
AUTH_BASE = "https://auth.openai.com"
PLATFORM_BASE = "https://platform.openai.com"
PLATFORM_OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
PLATFORM_OAUTH_REDIRECT_URI = f"{PLATFORM_BASE}/auth/callback"
PLATFORM_OAUTH_AUDIENCE = "https://api.openai.com/v1"
PLATFORM_AUTH0_CLIENT = (
    "eyJuYW1lIjoiYXV0aDAuanMtc3BhLWpzIiwiZW52Ijp7ImJyb3dzZXIiOnRydWV9LCJ2ZXJzaW9uIjoiOS4yMy4wIn0="
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
SEC_CH = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
REG_PASSWORD = "#A1234567890"
SENTINEL_SV = "20260124ceb8"  # starmiaoa 用的版本号

# ── Sentinel PoW（来源：starmiaoa/utils/sentinel.py）───────


class SentinelPoW:
    MAX_ATTEMPTS = 500_000

    def __init__(self, device_id: str):
        self.device_id = device_id
        self._sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        return format(h & 0xFFFFFFFF, "08x")

    def _config(self) -> list:
        perf_now = random.uniform(1000, 50000)
        return [
            "1920x1080",
            time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()),
            4294705152,
            random.random(),
            UA,
            f"https://sentinel.openai.com/sentinel/{SENTINEL_SV}/sdk.js",
            None,
            None,
            "en-US",
            random.random(),
            random.choice(["vendorSub-undefined", "plugins-undefined", "mimeTypes-undefined", "hardwareConcurrency-undefined"]),
            random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
            random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
            perf_now,
            self._sid,
            "",
            random.choice([4, 8, 12, 16]),
            time.time() * 1000 - perf_now,
        ]

    @staticmethod
    def _b64(data) -> str:
        return base64.b64encode(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()).decode()

    def _requirements_token(self) -> str:
        data = self._config()
        data[3] = 1
        data[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._b64(data)

    def _solve_pow(self, seed: str, difficulty: str) -> str:
        start = time.time()
        data = self._config()
        difficulty = str(difficulty or "0")
        diff_len = len(difficulty)
        for i in range(self.MAX_ATTEMPTS):
            data[3] = i
            data[9] = round((time.time() - start) * 1000)
            payload = self._b64(data)
            if self._fnv1a_32(seed + payload)[:diff_len] <= difficulty:
                return "gAAAAAB" + payload + "~S"
        return "gAAAAABwQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + self._b64("e")

    def build_token(self, session: Session, flow: str) -> str:
        """完整 PoW 流程：req → 解 PoW → 组装 token。"""
        requirements = self._requirements_token()
        resp = session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            data=json.dumps({"p": requirements, "id": self.device_id, "flow": flow}),
            headers={
                "Content-Type": "text/plain;charset=UTF-8",
                "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
                "Origin": "https://sentinel.openai.com",
                "User-Agent": UA,
                "sec-ch-ua": SEC_CH,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
            timeout=20,
        )
        data = resp.json() if resp.text else {}
        token = str(data.get("token") or "")
        if resp.status_code != 200 or not token:
            raise RuntimeError(f"sentinel/req failed http={resp.status_code} body={(resp.text or '')[:200]}")

        pow_data = data.get("proofofwork") or {}
        p_value = (
            self._solve_pow(str(pow_data.get("seed") or ""), str(pow_data.get("difficulty") or "0"))
            if pow_data.get("required") and pow_data.get("seed")
            else requirements
        )
        return json.dumps({"p": p_value, "t": "", "c": token, "id": self.device_id, "flow": flow}, separators=(",", ":"))


# ── headers ──────────────────────────────────────────────────

def _common_headers() -> dict:
    return {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": AUTH_BASE,
        "sec-ch-ua": SEC_CH,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": UA,
    }


def _json_headers(referer: str, device_id: str) -> dict:
    h = _common_headers()
    h["referer"] = referer
    h["oai-device-id"] = device_id
    return h


def _nav_headers(referer: str = "") -> dict:
    h = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "sec-ch-ua": SEC_CH,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": UA,
        "upgrade-insecure-requests": "1",
    }
    if referer:
        h["referer"] = referer
    return h


# ── 邮箱收码（复用现有 MS mail client）──────────────────────

def _wait_otp(email_main: str, after_ts: float) -> str | None:
    """用现有 gptreg mail 模块收码。"""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from gptreg.config import load_config
    from gptreg.mail.providers import build_mail_client, mail_identity_key

    cfg = load_config(root / "config.yaml")

    # 构造 account dict
    account = _load_account(email_main)
    if not account:
        print(f"  [✗] 邮箱 {email_main} 不在号池中")
        return None

    client = build_mail_client(account, proxy=PROXY, impersonate="chrome145")
    identity = mail_identity_key(account)

    # 排除已用码
    from gptreg.mail.providers import UsedCodeCache
    cache = UsedCodeCache(root / "data" / "used_otp_codes.json")
    exclude = cache.seen_codes(identity)

    mail_cfg = cfg.get("mail", {})
    code = client.wait_for_otp(
        after_ts=after_ts,
        timeout=int(mail_cfg.get("max_wait", 90)),
        interval=int(mail_cfg.get("poll_interval", 3)),
        settle_seconds=int(mail_cfg.get("settle_seconds", 5)),
        exclude_codes=exclude,
    )
    if code:
        cache.remember(identity, code, email=email_main, status="submitted")
    return code


def _load_account(email_main: str) -> dict | None:
    """从号池加载一个邮箱的凭据。"""
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[1]
    pool_file = root / "mail_pool.txt"
    if not pool_file.exists():
        return None
    for line in pool_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or not line.startswith(email_main):
            continue
        parts = line.split("----")
        if len(parts) < 4:
            continue
        return {
            "email": parts[0],
            "password": parts[1],
            "client_id": parts[2],
            "refresh_token": parts[3],
            "mail_type": "ms_oauth",
        }
    return None


# ── 主流程 ───────────────────────────────────────────────────

def test_register():
    # 取号
    email_main = "JohnOwens2952@outlook.com"
    account = _load_account(email_main)
    if not account:
        print(f"找不到 {email_main}")
        return

    alias_tag = secrets.token_hex(3)
    email = f"{email_main.split('@')[0]}+{alias_tag}@outlook.com"
    device_id = str(uuid.uuid4())
    pw = REG_PASSWORD
    first = random.choice(["James", "Robert", "John", "Michael", "David"])
    last = random.choice(["Smith", "Johnson", "Williams", "Brown", "Jones"])
    birthdate = f"{random.randint(1996, 2006):04d}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"

    print(f"\n{'='*60}")
    print(f"测试 starmiaoa 纯 Python PoW 注册")
    print(f"邮箱: {email} (主号: {email_main})")
    print(f"密码: {pw}")
    print(f"设备: {device_id[:16]}...")
    print(f"代理: {PROXY}")
    print(f"{'='*60}")

    s = Session(impersonate="chrome", verify=False)
    s.proxies = {"http": PROXY, "https": PROXY}
    s.timeout = 30

    # 设 oai-did cookie
    for domain in (".auth.openai.com", "auth.openai.com"):
        s.cookies.set("oai-did", device_id, domain=domain)

    pow_engine = SentinelPoW(device_id)
    t0 = time.time()

    # 预暖 CF
    print("\n[pre] 预暖 Cloudflare...")
    r = s.get("https://platform.openai.com/", timeout=30)
    print(f"  platform: {r.status_code}")
    r = s.get("https://auth.openai.com/", timeout=30)
    print(f"  auth: {r.status_code}")

    try:
        # ── Step 1: platform authorize (PKCE + signup) ──
        print("\n[1/8] platform authorize...")
        code_verifier, code_challenge = _generate_pkce()
        params = {
            "issuer": AUTH_BASE,
            "client_id": PLATFORM_OAUTH_CLIENT_ID,
            "audience": PLATFORM_OAUTH_AUDIENCE,
            "redirect_uri": PLATFORM_OAUTH_REDIRECT_URI,
            "device_id": device_id,
            "screen_hint": "signup",
            "max_age": "0",
            "scope": "openid profile email offline_access",
            "response_type": "code",
            "response_mode": "query",
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "auth0Client": PLATFORM_AUTH0_CLIENT,
        }
        url = f"{AUTH_BASE}/api/accounts/authorize?{_urlencode(params)}"
        r = s.get(url, headers=_nav_headers(PLATFORM_BASE + "/"), allow_redirects=True)
        final = str(r.url)
        print(f"  HTTP {r.status_code} → {final[:120]}")
        if r.status_code != 200 or "/create-account" not in final:
            print(f"  ✗ 未进入注册流: {final}")
            return
        print(f"  ✓ 进入注册页")

        # ── Step 2: authorize/continue (提交邮箱) ──
        print("\n[2/8] authorize/continue...")
        token_continue = pow_engine.build_token(s, "authorize_continue")
        h = _json_headers(f"{AUTH_BASE}/create-account", device_id)
        h["openai-sentinel-token"] = token_continue
        r = s.post(
            f"{AUTH_BASE}/api/accounts/authorize/continue",
            json={"username": {"kind": "email", "value": email}, "screen_hint": "signup"},
            headers=h,
        )
        data = _safe_json(r)
        page_type = str((data.get("page") or {}).get("type") or "")
        print(f"  HTTP {r.status_code} page={page_type}")
        if page_type != "create_account_password":
            print(f"  ✗ 期望 create_account_password，得到 {page_type}")
            if r.status_code == 200:
                print(f"  body: {json.dumps(data)[:300]}")
            return
        print(f"  ✓ 进入密码设置页")

        # ── Step 3: user/register (设密码) ──
        print("\n[3/8] user/register...")
        token_pwd = pow_engine.build_token(s, "username_password_create")
        print(f"  sentinel_token_len={len(token_pwd)}")
        try:
            st = json.loads(token_pwd)
            print(f"  sentinel keys={list(st.keys())} t_len={len(st.get('t',''))} c_len={len(st.get('c',''))}")
        except Exception:
            pass
        h = _json_headers(f"{AUTH_BASE}/create-account/password", device_id)
        h["openai-sentinel-token"] = token_pwd
        r = s.post(
            f"{AUTH_BASE}/api/accounts/user/register",
            json={"username": email, "password": pw},
            headers=h,
        )
        data = _safe_json(r)
        print(f"  HTTP {r.status_code}")
        if r.status_code != 200:
            msg = data.get("message", "")
            print(f"  ✗ 注册失败: {msg or (r.text or '')[:200]}")
            return
        print(f"  ✓ 密码设置成功")

        # ── Step 4: send OTP ──
        print("\n[4/8] send OTP...")
        r = s.get(
            f"{AUTH_BASE}/api/accounts/email-otp/send",
            headers=_nav_headers(f"{AUTH_BASE}/create-account/password"),
            allow_redirects=True,
        )
        print(f"  HTTP {r.status_code}")
        otp_after = time.time()

        # ── Step 5: wait OTP ──
        print("\n[5/8] 等待验证码...")
        code = _wait_otp(email_main, otp_after - 120)
        if not code:
            print("  ✗ 超时未收到验证码")
            return
        print(f"  ✓ 收到: {code}")

        # ── Step 6: validate OTP ──
        print("\n[6/8] validate OTP...")
        h = _json_headers(f"{AUTH_BASE}/email-verification", device_id)
        r = s.post(
            f"{AUTH_BASE}/api/accounts/email-otp/validate",
            json={"code": code},
            headers=h,
        )
        if r.status_code != 200:
            # 重试带 sentinel
            h["openai-sentinel-token"] = pow_engine.build_token(s, "authorize_continue")
            r = s.post(f"{AUTH_BASE}/api/accounts/email-otp/validate", json={"code": code}, headers=h)
        data = _safe_json(r)
        continue_url = str(data.get("continue_url") or "")
        page_type = str((data.get("page") or {}).get("type") or "")
        print(f"  HTTP {r.status_code} page={page_type}")
        if r.status_code != 200:
            print(f"  ✗ {r.text[:200]}")
            return
        print(f"  ✓ OTP 通过")

        # ── Step 7: create_account ──
        print("\n[7/8] create_account...")
        token_create = pow_engine.build_token(s, "oauth_create_account")
        h = _json_headers(f"{AUTH_BASE}/about-you", device_id)
        h["openai-sentinel-token"] = token_create
        r = s.post(
            f"{AUTH_BASE}/api/accounts/create_account",
            json={"name": f"{first} {last}", "birthdate": birthdate},
            headers=h,
            allow_redirects=False,
        )
        data = _safe_json(r)
        auth_code = ""
        cont = str(data.get("continue_url") or "")
        if cont:
            auth_code = _extract_code(cont)
        if not auth_code and r.status_code in (302, 303):
            auth_code = _extract_code(r.headers.get("Location", ""))
        print(f"  HTTP {r.status_code} code={'✓' if auth_code else '✗'}")
        if not auth_code:
            print(f"  ✗ {json.dumps(data)[:300]}")
            return
        print(f"  ✓ 账号创建成功")

        # ── Step 8: exchange tokens ──
        print("\n[8/8] oauth/token...")
        r = s.post(
            f"{AUTH_BASE}/api/accounts/oauth/token",
            headers={
                "accept": "*/*",
                "auth0-client": PLATFORM_AUTH0_CLIENT,
                "content-type": "application/json",
                "origin": PLATFORM_BASE,
                "referer": f"{PLATFORM_BASE}/",
                "sec-ch-ua": SEC_CH,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "user-agent": UA,
            },
            json={
                "client_id": PLATFORM_OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": PLATFORM_OAUTH_REDIRECT_URI,
            },
            timeout=60,
        )
        tokens = _safe_json(r)
        at = str(tokens.get("access_token") or "")
        rt = str(tokens.get("refresh_token") or "")
        print(f"  HTTP {r.status_code}")
        if not at:
            print(f"  ✗ {json.dumps(tokens)[:300]}")
            return
        print(f"  ✓ access_token={at[:24]}... refresh_token={rt[:24] if rt else '无'}...")

        # ── 结果 ──
        elapsed = time.time() - t0
        print(f"\n{'='*60}")
        print(f"🎉 注册成功！耗时 {elapsed:.0f}s")
        print(f"  邮箱:     {email}")
        print(f"  密码:     {pw}")
        print(f"  AT:       {at[:40]}...")
        print(f"  RT:       {rt[:40] if rt else '无'}...")
        print(f"  姓名:     {first} {last}")
        print(f"  生日:     {birthdate}")
        print(f"{'='*60}")

        # 保存
        result = {
            "email": email,
            "password": pw,
            "access_token": at,
            "refresh_token": rt,
            "name": f"{first} {last}",
            "birthdate": birthdate,
            "device_id": device_id,
            "method": "starmiaoa_pure_pow",
            "elapsed_s": round(elapsed, 1),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        out_path = f"D:/home/06_projects/GPT协议注册机/output/test_pow_{email.replace('@','_at_')}.json"
        json.dump(result, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  结果: {out_path}")

    except Exception as e:
        elapsed = time.time() - t0
        print(f"\n✗ 失败 ({elapsed:.0f}s): {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        s.close()


# ── helpers ──────────────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    import hashlib
    cv = secrets.token_urlsafe(64)
    cc = base64.urlsafe_b64encode(hashlib.sha256(cv.encode()).digest()).rstrip(b"=").decode()
    return cv, cc


def _urlencode(params: dict) -> str:
    from urllib.parse import urlencode
    return urlencode(params)


def _safe_json(resp) -> dict:
    try:
        return resp.json() if resp.text else {}
    except Exception:
        return {}


def _extract_code(url: str) -> str:
    from urllib.parse import parse_qs, urlparse
    if not url or "code=" not in url:
        return ""
    try:
        return str((parse_qs(urlparse(url).query).get("code") or [""])[0])
    except Exception:
        return ""


if __name__ == "__main__":
    test_register()

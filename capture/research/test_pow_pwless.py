# -*- coding: utf-8 -*-
"""测试纯 Python PoW + chatgpt 客户端 passwordless 路线。"""
import base64, hashlib, json, random, secrets, sys, time, uuid
from urllib.parse import urlencode

from curl_cffi.requests import Session

PROXY = "http://127.0.0.1:7890"
AUTH = "https://auth.openai.com"
CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
SEC_CH = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
AUTH0 = "eyJuYW1lIjoiYXV0aDAuanMtc3BhLWpzIiwiZW52Ijp7ImJyb3dzZXIiOnRydWV9LCJ2ZXJzaW9uIjoiOS4yMy4wIn0="
SENTINEL_SV = "20260124ceb8"


class PowSentinel:
    @staticmethod
    def _fnv1a(text):
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

    def _config(self):
        pn = random.uniform(1000, 50000)
        return [
            "1920x1080",
            time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()),
            4294705152,
            random.random(),
            UA,
            f"https://sentinel.openai.com/sentinel/{SENTINEL_SV}/sdk.js",
            None, None, "en-US",
            random.random(),
            random.choice(["vendorSub-undefined", "plugins-undefined"]),
            random.choice(["location", "implementation"]),
            random.choice(["Object", "Function"]),
            pn, str(uuid.uuid4()), "", random.choice([4, 8, 12, 16]),
            time.time() * 1000 - pn,
        ]

    def _b64(self, data):
        return base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()

    def _requirements(self):
        d = self._config()
        d[3] = 1
        d[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._b64(d)

    def build(self, session, device_id, flow):
        req = self._requirements()
        r = session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            data=json.dumps({"p": req, "id": device_id, "flow": flow}),
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
        data = r.json() if r.text else {}
        token = str(data.get("token") or "")
        if r.status_code != 200 or not token:
            raise RuntimeError(f"sentinel fail http={r.status_code}")
        p_val = req
        pw = data.get("proofofwork") or {}
        if pw.get("required") and pw.get("seed"):
            seed, diff = str(pw["seed"]), str(pw.get("difficulty", "0"))
            cfg = self._config()
            start = time.time()
            for i in range(500000):
                cfg[3] = i
                cfg[9] = round((time.time() - start) * 1000)
                pl = self._b64(cfg)
                if self._fnv1a(seed + pl)[: len(diff)] <= diff:
                    p_val = "gAAAAAB" + pl + "~S"
                    break
        return json.dumps(
            {"p": p_val, "t": "", "c": token, "id": device_id, "flow": flow},
            separators=(",", ":"),
        )


def main():
    email_main = "JohnOwens2952@outlook.com"
    email = f"{email_main.split('@')[0]}+{secrets.token_hex(3)}@outlook.com"
    did = str(uuid.uuid4())
    t0 = time.time()

    print(f"=== 纯 Python PoW + passwordless 测试 ===")
    print(f"邮箱: {email}")

    s = Session(impersonate="chrome", verify=False)
    s.proxies = {"http": PROXY, "https": PROXY}
    s.timeout = 30
    s.cookies.set("oai-did", did, domain=".auth.openai.com")
    pow_engine = PowSentinel()

    # Step 1: authorize
    print("\n[1] authorize...")
    cv = secrets.token_urlsafe(64)
    cc = base64.urlsafe_b64encode(hashlib.sha256(cv.encode()).digest()).rstrip(b"=").decode()
    params = {
        "issuer": AUTH, "client_id": CLIENT_ID,
        "audience": "https://api.openai.com/v1",
        "redirect_uri": "https://chatgpt.com/api/auth/callback/openai",
        "device_id": did, "screen_hint": "signup", "max_age": "0",
        "scope": "openid profile email offline_access model.request model.read organization.read organization.write",
        "response_type": "code", "response_mode": "query",
        "state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
        "code_challenge": cc, "code_challenge_method": "S256", "auth0Client": AUTH0,
    }
    h = {
        "accept": "text/html,*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
        "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
        "referer": "https://chatgpt.com/",
    }
    r = s.get(f"{AUTH}/api/accounts/authorize?{urlencode(params)}", headers=h, allow_redirects=True)
    print(f"  HTTP {r.status_code} → {str(r.url)[:100]}")

    # Step 2: authorize/continue
    print("\n[2] authorize/continue...")
    h2 = {
        "accept": "application/json", "content-type": "application/json",
        "origin": AUTH, "user-agent": UA, "sec-ch-ua": SEC_CH,
        "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
        "referer": f"{AUTH}/create-account", "oai-device-id": did,
    }
    r = s.post(f"{AUTH}/api/accounts/authorize/continue",
               json={"username": {"kind": "email", "value": email}, "screen_hint": "signup"}, headers=h2)
    data = r.json() if r.text else {}
    pt = str((data.get("page") or {}).get("type") or "")
    print(f"  HTTP {r.status_code} page={pt}")

    if pt not in ("email_otp_verification", "email_otp_verification_registration"):
        print(f"  意外页面: {pt}, 结束")
        return

    # Step 3: send OTP
    print("\n[3] send OTP...")
    r = s.get(f"{AUTH}/api/accounts/email-otp/send",
              headers={
                  "accept": "text/html,*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
                  "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                  "referer": f"{AUTH}/email-verification", "upgrade-insecure-requests": "1",
              }, allow_redirects=True)
    print(f"  HTTP {r.status_code}")
    otp_after = time.time()

    # Step 4: wait OTP
    print("\n[4] 等待验证码...")
    sys.path.insert(0, "D:/home/06_projects/GPT协议注册机")
    from gptreg.config import load_config
    from gptreg.mail.providers import build_mail_client, mail_identity_key, UsedCodeCache

    cfg = load_config("config.yaml")
    acc = {
        "email": email_main,
        "password": "bgndpwm2638",
        "client_id": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        "refresh_token": "M.C505_BL2.0.U.MsaArtifacts.-CqgfP2fFdYCGC7bQK!ySIeQFmRZmEqUjVolU1iJwwihMVblZ!bgHhUPc4NLkoJb78KOhneINS1TT6r*Ke5dYrPQqPNyLmPDmAFjPaVJlkHft6XvrOiTsUrfPRsh8n5X5SFQpqojM3EIXTbLDxzhu4fLJqtG*5kvxmUEXsk0hhg8IDKfVo9!nDwEG0hQdJPmZOmIwTlG6KKxY8TkG4fw9rsprmQ86KxjtxBBpx6QS7e9ie0TtWvJufx3h7afLz0EJ*QMh77nXthZBVpaUv0W8MtG9*WG8v5XP6PKdVEP8ipOTBSdsDsKPYlOc!YbYilZaht8VhFH3DwLw5Sk7P7chdvAXCOBpwqW3hd7J!mAY5TV!UTp7yNQlwQr9HHVaqvG8XNc2Yruv00C2q4SNIkqSvl6JSNRGqB7royrhqkpE3hMG0!DBNU5dFz2tl!6jmEgHNg$$",
        "mail_type": "ms_oauth",
    }
    client = build_mail_client(acc, proxy=PROXY, impersonate="chrome145")
    cache = UsedCodeCache("data/used_otp_codes.json")
    code = client.wait_for_otp(
        after_ts=otp_after - 120, timeout=90, interval=3, settle_seconds=5,
        exclude_codes=cache.seen_codes(mail_identity_key(acc)),
    )
    if not code:
        print("  ✗ 超时")
        return
    print(f"  ✓ {code}")

    # Step 5: validate OTP
    print("\n[5] validate OTP...")
    token = pow_engine.build(s, did, "authorize_continue")
    h3 = {**h2, "referer": f"{AUTH}/email-verification"}
    h3["openai-sentinel-token"] = token
    r = s.post(f"{AUTH}/api/accounts/email-otp/validate", json={"code": code}, headers=h3)
    data = r.json() if r.text else {}
    print(f"  HTTP {r.status_code} page={str((data.get('page') or {}).get('type') or '')}")

    # Step 6: create_account
    print("\n[6] create_account...")
    token2 = pow_engine.build(s, did, "oauth_create_account")
    h4 = {**h2, "referer": f"{AUTH}/about-you"}
    h4["openai-sentinel-token"] = token2
    first = random.choice(["James", "Robert", "John"])
    last = random.choice(["Smith", "Johnson", "Brown"])
    bday = f"{random.randint(1996, 2006):04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
    r = s.post(f"{AUTH}/api/accounts/create_account",
               json={"name": f"{first} {last}", "birthdate": bday}, headers=h4, allow_redirects=False)
    data = r.json() if r.text else {}
    cont = str(data.get("continue_url") or "")
    print(f"  HTTP {r.status_code} continue_url={'✓' if cont else '✗'}")

    if not cont:
        return

    # Step 7: callback + session
    print("\n[7] callback + session...")
    r = s.get(cont,
              headers={
                  "accept": "text/html,*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
                  "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                  "referer": f"{AUTH}/about-you",
              }, allow_redirects=True)
    r = s.get("https://chatgpt.com/api/auth/session",
              headers={
                  "accept": "*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
                  "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                  "referer": "https://chatgpt.com/", "oai-device-id": did,
              })
    data = r.json() if r.text else {}
    at = str(data.get("accessToken") or "")
    if not at:
        print(f"  ✗ 无 AT")
        return
    print(f"  ✓ AT={at[:30]}...")

    # Step 8: health check
    print("\n[8] health check...")
    h5 = {
        "accept": "*/*", "user-agent": UA,
        "authorization": f"Bearer {at}", "oai-device-id": did,
        "oai-language": "en-US", "referer": "https://chatgpt.com/",
    }
    r = s.get("https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27", headers=h5)
    body = (r.text or "")[:200]
    print(f"  HTTP {r.status_code} body={body}")

    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"✓ 注册成功! [{elapsed:.0f}s]")
    print(f"  邮箱: {email}")
    print(f"  AT:   {at[:40]}...")
    print(f"{'='*50}")

    # 保存
    result = {
        "email": email, "access_token": at, "name": f"{first} {last}",
        "birthdate": bday, "device_id": did, "method": "pure_pow_passwordless",
        "elapsed_s": round(elapsed, 1),
    }
    out = f"D:/home/06_projects/GPT协议注册机/output/test_pow2_{email.replace('@', '_at_')}.json"
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  结果: {out}")

    s.close()


if __name__ == "__main__":
    main()

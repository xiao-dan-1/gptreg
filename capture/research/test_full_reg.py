"""完整注册测试：纯 Python PoW + passwordless OTP 路线。"""
import base64, hashlib, json, random, secrets, sys, time, uuid
from urllib.parse import urlencode

from curl_cffi.requests import Session

PROXY = "http://127.0.0.1:7890"
AUTH = "https://auth.openai.com"
CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
SEC_CH = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
AUTH0 = "eyJuYW1lIjoiYXV0aDAuanMtc3BhLWpzIiwiZW52Ijp7ImJyb3dzZXIiOnRydWV9LCJ2ZXJzaW9uIjoiOS4yMy4wIn0="
PASSWORD = "#A1234567890"

# 从号池取第一个待用号
ROOT = r"D:\home\06_projects\GPT协议注册机"
sys.path.insert(0, ROOT)

# 直接指定 BrandonNichols1400
email_main = "BrandonNichols1400@outlook.com"
mail_pass = "agfquww581515"
mail_cid = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
mail_rt = "M.C539_BL2.0.U.MsaArtifacts.-Cm6tTvgGQgNdpX5hGgJljDnkHy9QEpWjUWgsqWlEW03WnYwgu!5CNx4FOcu5JI6UBVjxmWRuwjHDaWpAXOaE0jSWekRk81FCvEml*nhLveZlpSbPPnYrvgx84yBwGOoPZ*GkvRyffrmD8kwgPKcXZls7pczQjZj!HPUDOjXkZa!bA5JMRQggetyRY1dkyTntYnIpVyVpEVHyfElpzxPpm5aco65QVL9HcksBgSAFXEsHMgSoza5frswYA9yPLFzpvA*2LqCj8fUka3OSU0tcZ2i8sIj3qkEvKU0NMnfnZEZbg6UBA0lIf3Sx9H6cQhdIBfKz2iaH3FJduDj8JIVtBrgNavdq76z3sMJizEad2q1lWGssLslG6xjyLK!6jLgMRGgnqnnZDiPv6LuccGQFjQU5fJqja8X04JPUT9b!CbbYkpwAnMzNr7ACUHKa8JtKVduBbreKhuX8DMP7VuhG1Sw"

tag = secrets.token_hex(3)
email = f"{email_main.split('@')[0]}+{tag}@outlook.com"
did = str(uuid.uuid4())
t0 = time.time()

print(f"=== 完整注册: {email} ===")

# ── SentinelPoW（来自 gptreg.sentinel） ──
from gptreg.sentinel import SentinelPoW

pow = SentinelPoW(ua=UA)

# ── Session ──
s = Session(impersonate="chrome", verify=False)
s.proxies = {"http": PROXY, "https": PROXY}
s.timeout = 30
s.cookies.set("oai-did", did, domain=".auth.openai.com")

try:
    # [1] authorize
    print("[1/8] authorize...")
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
    h = {"accept": "text/html,*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
         "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"', "referer": "https://chatgpt.com/"}
    r = s.get(f"{AUTH}/api/accounts/authorize?{urlencode(params)}", headers=h, allow_redirects=True)
    assert r.status_code == 200 and "/create-account" in str(r.url), f"authorize failed: {r.status_code} {str(r.url)[:100]}"
    print(f"  OK {time.time()-t0:.0f}s")

    # [2] authorize/continue
    print("[2/8] authorize/continue...")
    h2 = {"accept": "application/json", "content-type": "application/json", "origin": AUTH,
          "user-agent": UA, "sec-ch-ua": SEC_CH, "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
          "referer": f"{AUTH}/create-account", "oai-device-id": did}
    r = s.post(f"{AUTH}/api/accounts/authorize/continue",
               json={"username": {"kind": "email", "value": email}, "screen_hint": "signup"}, headers=h2)
    data = r.json()
    pt = str((data.get("page") or {}).get("type") or "")
    assert r.status_code == 200 and pt in ("email_otp_verification", "email_otp_verification_registration"), \
        f"continue failed: {r.status_code} page={pt}"
    print(f"  OK page={pt}")

    # [3] send OTP
    print("[3/8] send OTP...")
    r = s.get(f"{AUTH}/api/accounts/email-otp/send",
              headers={"accept": "text/html,*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
                       "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                       "referer": f"{AUTH}/email-verification", "upgrade-insecure-requests": "1"},
              allow_redirects=True)
    assert r.status_code in (200, 302), f"send OTP failed: {r.status_code}"
    otp_after = time.time()
    print(f"  OK {time.time()-t0:.0f}s")

    # [4] wait OTP
    print("[4/8] 等待验证码...")
    from gptreg.mail.providers import build_mail_client
    acc = {"email": email_main, "password": mail_pass, "client_id": mail_cid,
           "refresh_token": mail_rt, "mail_type": "ms_oauth"}
    client = build_mail_client(acc, proxy=PROXY, impersonate="chrome145")
    code = client.wait_for_otp(after_ts=otp_after - 120, timeout=90, interval=3, settle_seconds=5,
                               exclude_codes=set())
    assert code, "OTP 超时"
    print(f"  OK code={code} {time.time()-t0:.0f}s")

    # [5] validate OTP
    print("[5/8] validate OTP...")
    token_v = pow.build(s, did, "authorize_continue")
    h3 = {**h2, "referer": f"{AUTH}/email-verification"}
    h3["openai-sentinel-token"] = token_v
    r = s.post(f"{AUTH}/api/accounts/email-otp/validate", json={"code": code}, headers=h3)
    assert r.status_code == 200, f"validate failed: {r.status_code} {(r.text or '')[:200]}"
    print(f"  OK {time.time()-t0:.0f}s")

    # [6] warm about-you
    print("[6/8] warm about-you...")
    r = s.get(f"{AUTH}/about-you",
              headers={"accept": "text/html,*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
                       "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                       "referer": f"{AUTH}/email-verification", "upgrade-insecure-requests": "1"},
              allow_redirects=True)
    time.sleep(0.5)
    print(f"  OK")

    # [7] create_account
    print("[7/8] create_account...")
    token_c = pow.build(s, did, "oauth_create_account")
    h4 = {**h2, "referer": f"{AUTH}/about-you"}
    h4["openai-sentinel-token"] = token_c
    first = random.choice(["James", "Robert", "John", "Michael", "David"])
    last = random.choice(["Smith", "Johnson", "Williams", "Brown", "Jones"])
    bday = f"{random.randint(1996, 2006):04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
    r = s.post(f"{AUTH}/api/accounts/create_account",
               json={"name": f"{first} {last}", "birthdate": bday}, headers=h4, allow_redirects=False)
    data = r.json()
    cont = str(data.get("continue_url") or "")
    assert r.status_code in (200, 302, 303) and cont, \
        f"create_account failed: {r.status_code} {(r.text or '')[:200]}"
    print(f"  OK {time.time()-t0:.0f}s")

    # [8] callback + session + health
    print("[8/8] callback + session...")
    r = s.get(cont,
              headers={"accept": "text/html,*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
                       "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                       "referer": f"{AUTH}/about-you"}, allow_redirects=True)
    time.sleep(0.5)
    r = s.get("https://chatgpt.com/api/auth/session",
              headers={"accept": "*/*", "user-agent": UA, "sec-ch-ua": SEC_CH,
                       "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                       "referer": "https://chatgpt.com/", "oai-device-id": did})
    data = r.json()
    at = str(data.get("accessToken") or "")
    assert at, f"no accessToken: {str(data)[:200]}"
    print(f"  OK AT={at[:30]}...")

    # health check
    r = s.get("https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
              headers={"accept": "*/*", "user-agent": UA, "authorization": f"Bearer {at}",
                       "oai-device-id": did, "oai-language": "en-US", "referer": "https://chatgpt.com/"})
    body = (r.text or "")[:200]
    print(f"  health: {r.status_code} {body}")

    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"🎉 注册成功! [{elapsed:.0f}s]")
    print(f"  邮箱: {email}")
    print(f"  AT:   {at[:40]}...")
    print(f"  方法: pure-python-pow")
    print(f"{'='*50}")

    result = {"email": email, "access_token": at, "name": f"{first} {last}", "birthdate": bday,
              "device_id": did, "method": "pure_pow", "health_http": r.status_code,
              "health_body": body, "elapsed_s": round(elapsed, 1)}
    out = f"{ROOT}/output/reg_pow_{email.replace('@', '_at_')}.json"
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  结果: {out}")

    # 也追加到 accounts.jsonl
    with open(f"{ROOT}/output/accounts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")

except Exception as e:
    elapsed = time.time() - t0
    print(f"\n✗ 失败 [{elapsed:.0f}s]: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
finally:
    s.close()

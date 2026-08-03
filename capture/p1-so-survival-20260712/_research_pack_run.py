"""Zero-cost research pack: multi-flow chatReq + dx shape + A/B meta."""
from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from gptreg.config import load_config
from gptreg.proxyutil import resolve_proxy
from gptreg.sentinel import SentinelPoW

OUT = Path("capture/p1-so-survival-20260712/research_pack")
OUT.mkdir(parents=True, exist_ok=True)

cfg = load_config()
proxy = resolve_proxy(cfg, None)
print("proxy", proxy.label())

from curl_cffi import requests as creq

sess = creq.Session()
url = proxy.session_url or proxy.upstream_url
if url:
    sess.proxies = {"http": url, "https": url}

ua = (cfg.get("browser") or {}).get("user_agent") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
pow_engine = SentinelPoW(ua=ua)
device_id = str(uuid.uuid4())

flows = ["authorize_continue", "oauth_create_account", "username_password_create"]
obs_all: dict = {}
raw_slim: dict = {}

for flow in flows:
    try:
        tok = pow_engine.build(sess, device_id, flow)
        obs = dict(pow_engine.last_chatreq_obs or {})
        cr = dict(pow_engine.last_chatreq or {})
        so = cr.get("so") if isinstance(cr.get("so"), dict) else {}
        ts = cr.get("turnstile") if isinstance(cr.get("turnstile"), dict) else {}
        pw = cr.get("proofofwork") if isinstance(cr.get("proofofwork"), dict) else {}
        parsed = json.loads(tok)
        raw_slim[flow] = {
            "keys": sorted(cr.keys()),
            "persona": cr.get("persona"),
            "expire_after": cr.get("expire_after"),
            "token_len": len(str(cr.get("token") or "")),
            "proofofwork": {
                "required": pw.get("required"),
                "difficulty": pw.get("difficulty"),
                "seed_len": len(str(pw.get("seed") or "")),
                "seed_prefix": str(pw.get("seed") or "")[:20],
            },
            "turnstile": {
                "required": ts.get("required"),
                "dx_len": len(ts.get("dx") or "") if isinstance(ts.get("dx"), str) else 0,
                "dx_prefix": (ts.get("dx") or "")[:40] if isinstance(ts.get("dx"), str) else None,
                "keys": sorted(ts.keys()) if ts else [],
            },
            "so": {
                "required": so.get("required"),
                "keys": sorted(so.keys()) if so else [],
                "collector_dx_len": len(so.get("collector_dx") or "")
                if isinstance(so.get("collector_dx"), str)
                else 0,
                "snapshot_dx_len": len(so.get("snapshot_dx") or "")
                if isinstance(so.get("snapshot_dx"), str)
                else 0,
                "collector_dx_prefix": (so.get("collector_dx") or "")[:60]
                if isinstance(so.get("collector_dx"), str)
                else None,
                "snapshot_dx_prefix": (so.get("snapshot_dx") or "")[:60]
                if isinstance(so.get("snapshot_dx"), str)
                else None,
            },
            "token_json_keys": list(parsed.keys()),
            "t_len": len(parsed.get("t") or ""),
        }
        obs_all[flow] = obs
        print(
            flow,
            "requires",
            obs.get("requires"),
            "so_req",
            obs.get("so_required"),
            "cdx",
            obs.get("so_collector_dx_len"),
            "sdx",
            raw_slim[flow]["so"]["snapshot_dx_len"],
            "tdx",
            obs.get("turnstile_dx_len"),
        )
    except Exception as e:
        obs_all[flow] = {"error": f"{type(e).__name__}: {e}"}
        raw_slim[flow] = {"error": f"{type(e).__name__}: {e}"}
        print(flow, "ERR", e)


def peek_dx(s: str | None) -> dict | None:
    if not s:
        return None
    out: dict = {"len": len(s), "prefix": s[:80]}
    for pad in ("", "=", "==", "==="):
        try:
            raw = base64.b64decode(s + pad, validate=False)
            out["b64_ok"] = True
            out["b64_len"] = len(raw)
            out["b64_head_hex"] = raw[:16].hex()
            try:
                out["b64_utf8_prefix"] = raw.decode("utf-8")[:120]
            except Exception:
                out["b64_utf8"] = False
            break
        except Exception:
            continue
    else:
        out["b64_ok"] = False
    return out


try:
    pow_engine.build(sess, device_id, "oauth_create_account")
    cr = pow_engine.last_chatreq or {}
    so = cr.get("so") or {}
    ts = cr.get("turnstile") or {}
    dx_peek = {
        "collector_dx": peek_dx(so.get("collector_dx") if isinstance(so, dict) else None),
        "snapshot_dx": peek_dx(so.get("snapshot_dx") if isinstance(so, dict) else None),
        "turnstile_dx": peek_dx(ts.get("dx") if isinstance(ts, dict) else None),
    }
except Exception as e:
    dx_peek = {"error": str(e)}

accounts = []
for ln in Path("output/accounts.jsonl").read_text(encoding="utf-8").splitlines():
    if not ln.strip():
        continue
    d = json.loads(ln)
    em = d.get("email") or ""
    if "BrandonNichols1400" in em or "EricWilliams3405" in em:
        accounts.append(
            {
                "email": em,
                "saved_at": d.get("saved_at") or d.get("created_at"),
                "device_id": d.get("device_id"),
                "sentinel": d.get("sentinel") or d.get("sentinel_obs") or {},
                "has_access": bool(d.get("access_token")),
                "proxy": d.get("proxy") or d.get("proxy_label"),
                "keys": sorted(d.keys()),
            }
        )

a_log = Path("capture/p1-so-survival-20260712/run_A_browser.log").read_text(
    encoding="utf-8", errors="ignore"
)
b_log = Path("capture/p1-so-survival-20260712/run_B_pow.log").read_text(
    encoding="utf-8", errors="ignore"
)
log_hits = {
    "A_has_so_lines": re.findall(r".*has_so=.*", a_log)[:12],
    "A_chatReq": re.findall(r".*\[Sentinel/chatReq\].*", a_log)[:12],
    "A_browser": re.findall(r".*mode=browser.*|.*browser.*so.*", a_log)[:12],
    "B_has_so_lines": re.findall(r".*has_so=.*", b_log)[:12],
    "B_chatReq": re.findall(r".*\[Sentinel/chatReq\].*", b_log)[:15],
    "B_warn_so": re.findall(r".*要求 so.*|.*so_required.*", b_log)[:10],
    "B_disallowed": re.findall(r".*registration_disallowed.*|.*disallowed.*", b_log)[:5],
}

sdk = Path("vendor/sentinel/sdk.js")
sdk_info: dict = {}
sdk_context = None
if sdk.exists():
    t = sdk.read_text(encoding="utf-8", errors="ignore")
    sdk_info["size"] = len(t)
    for name in [
        "sessionObserverToken",
        "collector_dx",
        "snapshot_dx",
        "openai-sentinel-so-token",
    ]:
        sdk_info[name] = t.find(name)
    i = t.find("sessionObserverToken")
    if i >= 0:
        sdk_context = t[max(0, i - 200) : i + 500]

pack = {
    "ts": datetime.now().isoformat(timespec="seconds"),
    "device_id": device_id,
    "multi_flow_obs": obs_all,
    "multi_flow_slim": raw_slim,
    "dx_peek": dx_peek,
    "accounts_ab": accounts,
    "log_hits": log_hits,
    "sdk_info": sdk_info,
    "sdk_context": sdk_context,
}
(OUT / "multi_flow_chatreq.json").write_text(
    json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("wrote", OUT / "multi_flow_chatreq.json")

print("\n=== MULTI-FLOW TABLE ===")
for f, s in raw_slim.items():
    if "error" in s:
        print(f, "ERR", s["error"])
        continue
    print(
        f"{f:28} so={s['so']['required']} ts={s['turnstile']['required']} "
        f"pow={s['proofofwork']['required']} cdx={s['so']['collector_dx_len']} "
        f"sdx={s['so']['snapshot_dx_len']} tdx={s['turnstile']['dx_len']} c={s['token_len']}"
    )

try:
    proxy.close()
except Exception:
    pass

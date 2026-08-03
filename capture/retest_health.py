#!/usr/bin/env python3
"""Delayed health retest for captured/protocol tokens.

Uses gptreg.auth.check_account_health + BrowserSession (curl_cffi), not ad-hoc fetch.
Writes JSON under the account's session/output path.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gptreg import auth  # noqa: E402
from gptreg.config import load_config  # noqa: E402
from gptreg.session import BrowserSession  # noqa: E402


def jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        pad = "=" * ((4 - len(part) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(part + pad))
    except Exception:
        return {}


def find_is_deactivated(obj, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            pth = f"{path}.{k}" if path else k
            if k == "is_deactivated":
                out.append((pth, v))
            out.extend(find_is_deactivated(v, pth))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            out.extend(find_is_deactivated(v, f"{path}[{i}]"))
    return out


def load_token_sources() -> list[dict]:
    items: list[dict] = []
    browser_paths = [
        ROOT / "capture/session-retry-20260711-211251/browser_account_token.json",
        ROOT / "capture/session-20260711-205528/browser_account_token.json",
    ]
    for p in browser_paths:
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        items.append(
            {
                "label": p.parent.name,
                "source": "browser",
                "email": d.get("email"),
                "access_token": d.get("access_token"),
                "device_id": d.get("oai_did") or d.get("device_id"),
                "captured_at": d.get("captured_at"),
                "out_path": p.parent / "retest_health.json",
            }
        )

    acc_path = ROOT / "output/accounts.jsonl"
    if acc_path.exists():
        for line in acc_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            email = d.get("email") or "unknown"
            safe = email.replace("@", "_at_").replace("+", "_")
            items.append(
                {
                    "label": f"protocol:{email}",
                    "source": "protocol",
                    "email": email,
                    "access_token": d.get("access_token"),
                    "device_id": d.get("device_id"),
                    "captured_at": d.get("saved_at"),
                    "out_path": ROOT / "output" / f"retest_{safe}.json",
                }
            )
    return items


def retest_one(item: dict, cfg: dict, proxy: str) -> dict:
    token = item.get("access_token") or ""
    payload = jwt_payload(token)
    auth_claims = payload.get("https://api.openai.com/auth") or {}
    iat = payload.get("iat")
    age_min = round((time.time() - iat) / 60, 1) if iat else None

    session = BrowserSession(cfg, proxy=proxy)
    if item.get("device_id"):
        session.device_id = item["device_id"]
        try:
            for domain in (
                ".chatgpt.com",
                "chatgpt.com",
                ".openai.com",
                "auth.openai.com",
                "sentinel.openai.com",
            ):
                session.session.cookies.set("oai-did", session.device_id, domain=domain)
        except Exception:
            pass

    health = auth.check_account_health(session, token)

    # also hit /me for raw evidence
    me = {"status": None, "body": ""}
    try:
        headers = session.chatgpt_headers(referer="https://chatgpt.com/")
        headers["authorization"] = f"Bearer {token}"
        headers["oai-device-id"] = session.device_id
        headers["oai-language"] = (cfg.get("browser", {}) or {}).get("language", "en-US")
        headers.pop("content-type", None)
        resp = session.get("https://chatgpt.com/backend-api/me", headers=headers)
        me = {"status": resp.status_code, "body": (resp.text or "")[:800]}
    except Exception as exc:
        me = {"status": None, "error": str(exc)}

    deactivated = []
    plan = []
    body = health.get("body") or ""
    if health.get("http") == 200 and body.startswith("{"):
        try:
            j = json.loads(body if len(body) > 500 else body)
            # check_account_health truncates body to 500; re-fetch full check if needed
        except Exception:
            j = None
        if j is None or "accounts" not in (j or {}):
            try:
                headers = session.chatgpt_headers(referer="https://chatgpt.com/")
                headers["authorization"] = f"Bearer {token}"
                headers["oai-device-id"] = session.device_id
                headers.pop("content-type", None)
                resp = session.get(
                    "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                    headers=headers,
                )
                full = resp.text or ""
                health["body_full_len"] = len(full)
                if resp.status_code == 200 and full.startswith("{"):
                    j = json.loads(full)
                    health["body"] = full[:500]
            except Exception as exc:
                health["full_refetch_error"] = str(exc)
                j = None
        if isinstance(j, dict):
            deactivated = find_is_deactivated(j)[:6]
            # plan_type
            def find_plan(obj, path=""):
                out = []
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        pth = f"{path}.{k}" if path else k
                        if k == "plan_type":
                            out.append((pth, v))
                        out.extend(find_plan(v, pth))
                elif isinstance(obj, list):
                    for i, v in enumerate(obj[:50]):
                        out.extend(find_plan(v, f"{path}[{i}]"))
                return out

            plan = find_plan(j)[:4]

    result = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "label": item["label"],
        "source": item["source"],
        "email": item.get("email"),
        "captured_at": item.get("captured_at"),
        "device_id": session.device_id,
        "proxy": proxy or "direct",
        "token_age_min": age_min,
        "jwt": {
            "iat": iat,
            "exp": payload.get("exp"),
            "sub": payload.get("sub"),
            "plan": auth_claims.get("chatgpt_plan_type"),
            "account_id": auth_claims.get("chatgpt_account_id"),
            "is_signup": auth_claims.get("is_signup"),
        },
        "health": {
            "status": health.get("status"),
            "http": health.get("http"),
            "endpoint": health.get("endpoint"),
            "body_head": (health.get("body") or health.get("detail") or "")[:400],
            "is_deactivated": deactivated,
            "plan_type": plan,
        },
        "me": me,
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[], help="substring filter on email/label")
    ap.add_argument("--proxy", default="", help="override proxy; empty = config default 10808")
    ap.add_argument("--loop-min", type=float, default=0, help="if >0, retest every N minutes until Ctrl-C")
    ap.add_argument("--until-age", type=float, default=0, help="with --loop-min, stop after target age_min")
    args = ap.parse_args()

    cfg = load_config(ROOT / "config.yaml")
    # for retest, prefer stable local outbound proxy; avoid rotating lajiao unless asked
    proxy = args.proxy
    if not proxy:
        proxy = (cfg.get("proxy") or {}).get("default") or "http://127.0.0.1:10808"

    items = load_token_sources()
    if args.only:
        items = [
            it
            for it in items
            if any(s.lower() in (it.get("email") or "").lower() or s.lower() in it["label"].lower() for s in args.only)
        ]

    if not items:
        print("no tokens matched")
        return 1

    def run_once():
        results = []
        for it in items:
            print("=" * 60)
            print("retest", it["label"], it.get("email"))
            try:
                r = retest_one(it, cfg, proxy)
            except Exception as exc:
                r = {
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                    "label": it["label"],
                    "email": it.get("email"),
                    "error": str(exc),
                }
            results.append(r)
            outp = it["out_path"]
            outp.parent.mkdir(parents=True, exist_ok=True)
            # append-friendly: write latest + keep history file
            outp.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
            hist = outp.with_name(outp.stem + "_history.jsonl")
            with hist.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            h = r.get("health") or {}
            print(
                f"  age_min={r.get('token_age_min')} health={h.get('status')} http={h.get('http')}"
            )
            print(f"  is_deactivated={h.get('is_deactivated')}")
            print(f"  me_http={(r.get('me') or {}).get('status')} body_head={str((r.get('me') or {}).get('body') or (r.get('me') or {}).get('error') or '')[:160]}")
            print(f"  wrote {outp}")
        return results

    if args.loop_min and args.loop_min > 0:
        while True:
            results = run_once()
            if args.until_age:
                ages = [r.get("token_age_min") for r in results if r.get("token_age_min") is not None]
                if ages and min(ages) >= args.until_age:
                    print(f"reached until-age={args.until_age}, stop")
                    break
            print(f"sleep {args.loop_min} min ...")
            time.sleep(args.loop_min * 60)
    else:
        run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

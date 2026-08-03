#!/usr/bin/env python3
"""Analyze a capture session events.jsonl for registration protocol diffs."""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def hdr(headers: dict | None, name: str):
    if not headers:
        return None
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def find_entries(lines, substr: str):
    return [e for e in lines if substr in (e.get("url") or "")]


def main() -> int:
    session = Path(sys.argv[1] if len(sys.argv) > 1 else
                   r"D:/home/06_projects/GPT协议注册机/capture/session-retry-20260711-211251")
    p = session / "events.jsonl"
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    print("session", session)
    print("total events", len(lines))

    # create_account
    cas = find_entries(lines, "create_account")
    print("\n=== create_account entries", len(cas))
    ca = next((e for e in cas if e.get("phase") == "response" or e.get("status")), cas[0] if cas else None)
    so = st = None
    if ca:
        rh = ca.get("request_headers") or {}
        print("status", ca.get("status"), "method", ca.get("method"))
        print("url", ca.get("url"))
        print("all req header keys:", sorted(rh.keys(), key=str.lower))
        for k, v in rh.items():
            lk = k.lower()
            if "sentinel" in lk or lk.startswith("oai-") or lk in (
                "user-agent", "accept-language", "content-type", "origin", "referer", "authorization"
            ):
                show = v if len(str(v)) < 220 else str(v)[:200] + f"... len={len(v)}"
                print(f"  {k}: {show}")
        print("POST body:", ca.get("post_data"))
        if ca.get("body"):
            print("RESP body:", str(ca.get("body"))[:900])
        so = hdr(rh, "openai-sentinel-so-token")
        st = hdr(rh, "openai-sentinel-token")
        if so:
            print("\n--- so token ---")
            print("so len", len(so))
            print("so prefix", so[:60])
            try:
                pad = "=" * (-len(so) % 4)
                raw = base64.b64decode(so + pad)
                print("b64 decode len", len(raw))
                try:
                    print("utf8:", raw.decode("utf-8")[:400])
                except Exception:
                    print("raw head", raw[:100])
            except Exception as e:
                print("b64 fail", e)
        if st:
            print("\n--- sentinel-token ---")
            print("st len", len(st))
            try:
                j = json.loads(st)
                print("st json keys", list(j.keys()))
                for k, v in j.items():
                    if isinstance(v, str):
                        print(f"  {k}: len={len(v)} prefix={v[:50]}")
                    else:
                        print(f"  {k}:", v)
            except Exception as e:
                print("st not json", e, "prefix", st[:120])

    print("\n=== sentinel/req ===")
    for e in find_entries(lines, "sentinel/req"):
        if not (e.get("status") or e.get("phase") == "response"):
            continue
        print(e.get("ts"), e.get("status"), e.get("url", "")[:90])
        print("  post:", (e.get("post_data") or "")[:350])
        print("  resp:", (e.get("body") or "")[:350])

    print("\n=== email-otp ===")
    for e in find_entries(lines, "email-otp"):
        if not (e.get("status") or e.get("phase") == "response"):
            continue
        rh = e.get("request_headers") or {}
        print(e.get("ts"), e.get("method"), e.get("status"), urlparse(e.get("url") or "").path)
        for name in ("openai-sentinel-token", "openai-sentinel-so-token", "oai-device-id", "content-type"):
            v = hdr(rh, name)
            if v:
                print(f"  H {name}:", (str(v)[:140] + "...") if len(str(v)) > 140 else v)
        print("  post", (e.get("post_data") or "")[:220])
        print("  resp", (e.get("body") or "")[:350])

    print("\n=== auth flow ===")
    for key in ("signin/openai", "accounts/authorize", "callback/openai", "email-verification", "about-you"):
        for e in find_entries(lines, key):
            if e.get("status") or e.get("phase") == "response":
                print(e.get("ts"), e.get("method"), e.get("status"), urlparse(e.get("url") or "").path[:90])

    print("\n=== post-login key ===")
    for key in ("/backend-api/me", "accounts/check", "conversation/init", "chat-requirements", "api/auth/session"):
        for e in find_entries(lines, key):
            if e.get("status") or e.get("phase") == "response":
                rh = e.get("request_headers") or {}
                print(
                    e.get("ts"), e.get("method"), e.get("status"),
                    urlparse(e.get("url") or "").path[:70],
                    "did", bool(hdr(rh, "oai-device-id")),
                    "auth", bool(hdr(rh, "authorization")),
                )
                if "me" in key or "check" in key or "session" in key:
                    print("  resp", (e.get("body") or "")[:280])

    me_email = me_id = me_name = None
    for e in find_entries(lines, "/backend-api/me"):
        if e.get("body") and e.get("status") == 200:
            try:
                j = json.loads(e["body"])
                me_email, me_id, me_name = j.get("email"), j.get("id"), j.get("name")
                print("\nME email", me_email, "id", me_id, "name", me_name, "created", j.get("created"))
            except Exception as ex:
                print("me parse", ex)

    for e in find_entries(lines, "accounts/check"):
        if e.get("body") and e.get("status") == 200:
            try:
                j = json.loads(e["body"])

                def find(obj, key, path=""):
                    out = []
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            pth = f"{path}.{k}" if path else k
                            if k == key:
                                out.append((pth, v))
                            out.extend(find(v, key, pth))
                    elif isinstance(obj, list):
                        for i, v in enumerate(obj[:50]):
                            out.extend(find(v, key, f"{path}[{i}]"))
                    return out

                print("check is_deactivated", find(j, "is_deactivated")[:4])
                print("check plan_type", find(j, "plan_type")[:4])
            except Exception as ex:
                print("check parse", ex)

    extract = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "total_events": len(lines),
        "account": {"email": me_email, "id": me_id, "name": me_name},
        "create_account": ca,
        "has_so": bool(so),
        "so_len": len(so) if so else 0,
        "has_sentinel_token": bool(st),
        "sentinel_reqs": [e for e in find_entries(lines, "sentinel/req") if e.get("status") or e.get("phase") == "response"],
        "email_otp": [e for e in find_entries(lines, "email-otp") if e.get("status") or e.get("phase") == "response"],
        "post_login_paths": sorted({
            urlparse(e.get("url") or "").path
            for e in lines
            if "backend-api" in (e.get("url") or "") and (e.get("status") or e.get("phase") == "response")
        }),
    }
    outp = session / "analysis_extract.json"
    outp.write_text(json.dumps(extract, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nWROTE", outp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

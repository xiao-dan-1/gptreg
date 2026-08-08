#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校准真实存活率:区分 access_token 过期 vs 账号真死。

测活用 access_token(寿命 10 天),过期后返回 token_expired/invalidated ≠ 账号死亡。
本脚本解码每个账号 access_token 的 exp,与测活时刻对比,重算"校正后存活率"。

测活结果(2026-08-04 23:01 check_survival.py 输出)硬编码为 status map。
用法: python capture/calibrate_survival.py
"""
from __future__ import annotations

import base64
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# 测活时刻(本地)。用 08-05 00:00 保守近似(check_survival 运行在 08-04 23:01 后)。
CHECK_AT = datetime.datetime(2026, 8, 5, 0, 0)

# 测活结果:email -> status(来自 check_survival.py 输出)
# ok / invalidated / token_expired(输出里显示为 error,body 是 "Provided authentication token is expired")
CHECK_RESULTS = {
    # ---- ok(存活) ----
    "MaryAbbott5178+430f61@outlook.com": "ok",
    "EricWaller3362+e21c3c@outlook.com": "ok",
    "JeffreyBenson3667+57d51d@outlook.com": "ok",
    "DavidEdwards8748+ab3b65@outlook.com": "ok",
    "CharlesHarmon1878+f2ffb1@outlook.com": "ok",
    "PeggyWhite1859+e0cc70@outlook.com": "ok",
    # ---- invalidated(access_token 当时有效但被吊销) ----
    "DanielCampbell8797+564765@outlook.com": "invalidated",
    "ShawnaAnderson9445+e89d04@outlook.com": "invalidated",
    "CrystalKelly5814+fc7e53@outlook.com": "invalidated",
    "GinaVaughn6179+766f65@outlook.com": "invalidated",
    "JulianWilliams6554+ad1fc3@outlook.com": "invalidated",
    "DebraKelly1539+524c12@outlook.com": "invalidated",
    "BruceMontgomery7992+edcf5f@outlook.com": "invalidated",
    "RobertHamilton2209+832b27@outlook.com": "invalidated",
    "CharlesPalmer3350+c435ab@outlook.com": "invalidated",
    "LindaRogers7125+3b01c1@outlook.com": "invalidated",
    "DerrickMclean9927+4900c4@outlook.com": "invalidated",
    "JoseMason9198+5a4099@outlook.com": "invalidated",
    "LarryYoung3164+79e83e@outlook.com": "invalidated",
    "AlexanderThompson4228+9a8e40@outlook.com": "invalidated",
    "TonyMaldonado5751+d50286@outlook.com": "invalidated",
    "SaraPatterson8093+1cd049@outlook.com": "invalidated",
    "MasonHiggins9042+ff3b2c@outlook.com": "invalidated",
    "AnthonyCarey1706+f90277@outlook.com": "invalidated",
    "BrettPerez7024+bac6b1@outlook.com": "invalidated",
    "EricStone7144+60d780@outlook.com": "invalidated",
    "DanielMccoy9764+ef21da@outlook.com": "invalidated",
    "ReneeHernandez2572+92b360@outlook.com": "invalidated",
    "SergioWillis1008+dfe71c@outlook.com": "invalidated",
    "SherryPeck4613+914258@outlook.com": "invalidated",
    "WilliamMarshall8394+e3c646@outlook.com": "invalidated",
    "JesseConway2533+46a286@outlook.com": "invalidated",
    "MariaMathis7655+44e000@outlook.com": "invalidated",
    "DorothyPhillips1173+a4168a@outlook.com": "invalidated",
    "JessicaLambert7525+30762a@outlook.com": "invalidated",
    "SarahBailey4801+564570@outlook.com": "invalidated",
    "WilliamMalone8264+c794e8@outlook.com": "invalidated",
    "ShawnWhite9704+aba47a@outlook.com": "invalidated",
    # ---- token_expired(输出里 error,body 是 Provided authentication token is expired) ----
    "RoelfsWida92+1a@outlook.com": "token_expired",
    "RoelfsWida92+2d65fd@outlook.com": "token_expired",
    "ConderGord45+827a56@outlook.com": "token_expired",
    "JohnOwens2952+ae8294@outlook.com": "token_expired",
    "JohnOwens2952+41dcda@outlook.com": "token_expired",
    "BrandonNichols1400+c688c2@outlook.com": "token_expired",
    "EricWilliams3405+6af702@outlook.com": "token_expired",
    "JenniferPhillips9261+542af2@outlook.com": "token_expired",
    "JasmineMcconnell9909+1b5fcd@outlook.com": "token_expired",
    "BeatheFulwood6282+85b8d3@outlook.com": "token_expired",
    "EmbreeNicholas183+7fcc43@outlook.com": "token_expired",
    "BengelsdorfSalato25+bf3db9@outlook.com": "token_expired",
    "BrendaAllen5526+60b911@outlook.com": "token_expired",
    "ZacharyWright3866+a13988@outlook.com": "token_expired",
}


def jwt_exp(token: str) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """解码 JWT 返回 (iat, exp)。"""
    try:
        parts = token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        iat = datetime.datetime.fromtimestamp(payload["iat"]) if payload.get("iat") else None
        exp = datetime.datetime.fromtimestamp(payload["exp"]) if payload.get("exp") else None
        return iat, exp
    except Exception:
        return None, None


def main() -> int:
    rows = []
    for line in Path(ROOT / "output" / "accounts.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        email = d.get("email")
        if not email or not d.get("access_token"):
            continue
        status = CHECK_RESULTS.get(email)
        if status is None:
            continue
        iat, exp = jwt_exp(d["access_token"])
        so = d.get("sentinel_obs") or {}
        rows.append({
            "email": email,
            "mode": so.get("challenge_mode") or "unknown",
            "status": status,
            "iat": iat, "exp": exp,
            "valid_at_check": exp and exp > CHECK_AT,
        })

    print(f"账号总数(有测活结果): {len(rows)}   测活时刻: {CHECK_AT:%m-%d %H:%M}\n")

    # 交叉:测活状态 x token 在测活时是否有效
    def verdict(r):
        if r["status"] == "ok":
            return "存活"
        if r["valid_at_check"]:
            return "真死(测活时 token 有效却被吊销)"
        return "token 已过期(账号存活未知)"

    buckets: dict[str, list] = {}
    for r in rows:
        v = verdict(r)
        buckets.setdefault(v, []).append(r)

    order = ["存活", "真死(测活时 token 有效却被吊销)", "token 已过期(账号存活未知)"]
    for v in order:
        rs = buckets.get(v, [])
        print(f"== {v}: {len(rs)} 个 ==")
        for r in sorted(rs, key=lambda x: x["exp"] or datetime.datetime.min):
            exp_s = f"{r['exp']:%m-%d %H:%M}" if r["exp"] else "?"
            print(f"  [{r['mode']:<24}] {r['email'][:36]:<38} exp={exp_s} 测活={r['status']}")
        print()

    # 校正后统计(按模式)
    print("=== 校正后存活率(按模式) ===")
    by_mode: dict[str, dict[str, int]] = {}
    for r in rows:
        m = r["mode"]
        by_mode.setdefault(m, {"total": 0, "alive": 0, "dead": 0, "unknown": 0})
        b = by_mode[m]
        b["total"] += 1
        v = verdict(r)
        if v == "存活":
            b["alive"] += 1
        elif v == "真死(测活时 token 有效却被吊销)":
            b["dead"] += 1
        else:
            b["unknown"] += 1
    for m, b in sorted(by_mode.items()):
        pct = f"{b['alive'] / b['total'] * 100:.0f}%" if b["total"] else "-"
        print(f"  {m:<26} 总={b['total']:>2} 存活={b['alive']:>2} 真死={b['dead']:>2} 未知={b['unknown']:>2} (活率 {pct})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

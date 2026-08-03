"""E2+E1 合并：pow/browser 交替各 N 次（默认 N=3 → 共 6 根）。

Z4 真名池已在 random_display_name；本脚本只编排注册 + 记表。
用法: python capture/p1-so-survival-20260712/_run_e2_e1.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gptreg.cli import configure_logging  # noqa: E402
from gptreg.config import load_config  # noqa: E402
from gptreg.pipeline import run_batch  # noqa: E402

N_PAIRS = 3
OUT_DIR = Path(__file__).resolve().parent
LOG_PATH = OUT_DIR / "e2_e1_results.jsonl"
SUMMARY_PATH = OUT_DIR / "e2_e1_summary.json"


def _append(row: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    configure_logging(True)
    cfg = load_config()
    print(f"[E2+E1] plan: {N_PAIRS} pairs = {N_PAIRS} pow + {N_PAIRS} browser")
    print("[E2+E1] Z4 natural names via random_display_name")

    # fresh log for this run
    if LOG_PATH.exists():
        bak = LOG_PATH.with_suffix(f".jsonl.bak-{datetime.now().strftime('%H%M%S')}")
        LOG_PATH.rename(bak)
        print("[E2+E1] previous log ->", bak.name)

    results: list[dict] = []
    slot = 0
    for i in range(1, N_PAIRS + 1):
        for mode in ("pow", "browser"):
            slot += 1
            cfg.setdefault("protocol", {})["sentinel_source"] = mode
            t0 = time.time()
            print(f"\n======== slot {slot}/{N_PAIRS * 2} mode={mode} pair={i} ========")
            batch = run_batch(
                cfg,
                count=1,
                workers=1,
                delay=0.0,
                continue_on_fail=True,
                proxy=None,
            )
            r = batch[0] if batch else {"success": False, "error": "empty batch"}
            err = str(r.get("error") or "")
            row = {
                "slot": slot,
                "pair": i,
                "mode": mode,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "ok": bool(r.get("success")),
                "email": r.get("email"),
                "mail_main": r.get("mail_main"),
                "name": r.get("name"),
                "birthdate": r.get("birthdate"),
                "has_so": (r.get("sentinel_obs") or {}).get("has_so"),
                "so_len": (r.get("sentinel_obs") or {}).get("so_len"),
                "health": (r.get("health") or {}).get("status") if isinstance(r.get("health"), dict) else r.get("health"),
                "proxy_label": r.get("proxy_label"),
                "error": err[:400] if err else None,
                "registration_disallowed": "registration_disallowed" in err,
                "elapsed_s": round(time.time() - t0, 1),
            }
            results.append(row)
            _append(row)
            status = "OK" if row["ok"] else "FAIL"
            print(
                f"[{status}] mode={mode} email={row['email']} "
                f"has_so={row['has_so']} name={row['name']} "
                f"disallow={row['registration_disallowed']} err={row['error'] and row['error'][:120]}"
            )
            time.sleep(2)

    pow_rows = [r for r in results if r["mode"] == "pow"]
    br_rows = [r for r in results if r["mode"] == "browser"]
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_pairs": N_PAIRS,
        "z4_natural_names": True,
        "pow": {
            "n": len(pow_rows),
            "ok": sum(1 for r in pow_rows if r.get("ok")),
            "disallow": sum(1 for r in pow_rows if r.get("registration_disallowed")),
        },
        "browser": {
            "n": len(br_rows),
            "ok": sum(1 for r in br_rows if r.get("ok")),
            "disallow": sum(1 for r in br_rows if r.get("registration_disallowed")),
        },
        "results": results,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n======== SUMMARY ========")
    print(json.dumps({k: summary[k] for k in ("pow", "browser", "z4_natural_names")}, ensure_ascii=False, indent=2))
    print("wrote", SUMMARY_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

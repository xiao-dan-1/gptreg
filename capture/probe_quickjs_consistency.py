"""probe: 同 device_id 两次独立 requirements（两次 Node 进程），解码对比 p 是否漂移。

验证好友 A2/A3：真实 SDK 产出的 p 是否内嵌每次现算的 time_origin。
真浏览器 timeOrigin（页面加载时刻）是会话常数；若两次 p 的 time_origin 不同 → 漂移成立。

用法: python capture/probe_quickjs_consistency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "capture"))

from capture.decode_solved_tokens import analyze, decode_p  # noqa: E402
from gptreg.sentinel_quickjs import _ensure_sdk, _fingerprint_payload, _quickjs_script, _run_action  # noqa: E402


def main() -> int:
    device_id = "probe-consistency-0001"
    cfg = {"browser": {}}
    sv = "20260219f9f6"
    script = _quickjs_script()
    sdk_file = _ensure_sdk(None, sv, 60000)  # 已缓存，不联网

    runs = []
    uuid_calls_all = []
    for i in range(3):
        fp = _fingerprint_payload(cfg, device_id, sv)
        out = _run_action(script, sdk_file, "requirements", fp, 60000, env_extra={"TZ": "America/Los_Angeles"})
        request_p = str(out.get("request_p") or "")
        runs.append(request_p)
        uuids = list(out.get("uuid_calls") or [])
        uuid_calls_all.append(uuids)
        print(f"\n--- 第 {i + 1} 次 requirements ---")
        print(f"request_p 长度 = {len(request_p)}")
        decoded = decode_p(request_p)
        print(f"解码类型 = {type(decoded).__name__}")
        if isinstance(decoded, list):
            print(f"数组长度 = {len(decoded)}")
            for j, v in enumerate(decoded):
                print(f"  [{j:2}] {repr(v)}")
            print(f"crypto.randomUUID 调用 {len(uuids)} 次: {uuids[:5]}")
        else:
            print(f"原始内容 = {str(decoded)[:120]}")

    print("\n\n===== 对比三次 p 是否漂移 =====")
    r0, r1, r2 = (decode_p(p) for p in runs)
    if isinstance(r0, list) and isinstance(r1, list) and isinstance(r2, list):
        n = max(len(r0), len(r1), len(r2))
        for j in range(n):
            v0 = r0[j] if j < len(r0) else "<none>"
            v1 = r1[j] if j < len(r1) else "<none>"
            v2 = r2[j] if j < len(r2) else "<none>"
            same = v0 == v1 == v2
            flag = "" if same else "  <-- 漂移"
            print(f"[{j:2}] run1={repr(v0)}  run2={repr(v1)}  run3={repr(v2)}{flag}")
        # [14] 是否来自 crypto.randomUUID？
        if uuid_calls_all:
            for i, (dec, uuids) in enumerate(zip(runs, uuid_calls_all)):
                d = decode_p(dec)
                v14 = d[14] if isinstance(d, list) and len(d) > 14 else None
                hit = v14 in uuids if uuids else False
                print(f"[14] 来源检查 run{i + 1}: p[14]={v14!r} 在 randomUUID 返回值中: {hit}")
    else:
        print("p 不是 JSON 数组，无法逐字段对比；见上方解码结果。")
        if isinstance(r0, bytes):
            print("p 为二进制，可能需要逆向 SDK 的编码，先记录长度对比：",
                  len(r0), len(r1), len(r2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

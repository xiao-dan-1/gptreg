#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""同 challenge 下浏览器 t vs vm t 的字节级差异定位。

t 是 XOR 流加密;同 challenge → 相同 keystream 段 → 相同明文段字节相同。
差异段的分布模式能区分:
  - 成块差异 → 对应某几个指纹读取(localStorage/字体/时序),可定位可补
  - 均匀散布 → keystream 依赖前面的明文,或大量环境值不同

用法: python capture/analyze_t_diff.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def b64d(s: str) -> bytes:
    s2 = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s2 + "=" * (-len(s2) % 4))


def runs(a: bytes, b: bytes) -> list[tuple[int, int, str]]:
    """返回 [(start, end, 'same'|'diff')] 段列表, 对齐到 min(len) 处截断。"""
    n = min(len(a), len(b))
    out = []
    start = 0
    prev = a[0] == b[0]
    for i in range(1, n):
        cur = a[i] == b[i]
        if cur != prev:
            out.append((start, i, "same" if prev else "diff"))
            start = i
            prev = cur
    out.append((start, n, "same" if prev else "diff"))
    return out


def main() -> int:
    d = json.loads((ROOT / "data" / "same_challenge_result.json").read_text(encoding="utf-8"))
    bt = d.get("browser_t_b64") or ""
    vt = d.get("vm_t_b64") or ""
    if not bt or not vt:
        print("缺少 t 数据(需先跑 same_challenge_compare.py 保存)")
        return 1
    b = b64d(bt)
    v = b64d(vt)
    print(f"浏览器 t: {len(b)}B  vm t: {len(v)}B  差 {len(b) - len(v)}B")

    segs = runs(b, v)
    same_total = sum(e - s for s, e, k in segs if k == "same")
    diff_total = sum(e - s for s, e, k in segs if k == "diff")
    print(f"相同段 {same_total}B / 差异段 {diff_total}B (对齐区 {min(len(b), len(v))}B)")
    print(f"差异段数量: {sum(1 for _, _, k in segs if k == 'diff')}")
    print()
    for s, e, k in segs:
        if k == "diff":
            bb = b[s:e]
            vb = v[s:e]
            print(f"  DIFF [{s:4d}-{e:4d}] {e - s:3d}B  browser={bb[:20].hex(' ')}... vm={vb[:20].hex(' ')}...")
    print()
    print("=== 相同段概览(位置) ===")
    for s, e, k in segs:
        if k == "same":
            print(f"  SAME [{s:4d}-{e:4d}] {e - s:3d}B")
    print()
    # 头部 16B 明文预览(XOR 流里非零明文通常可读)
    for name, arr in [("browser", b), ("vm", v)]:
        head = arr[:40]
        printable = "".join(chr(c) if 32 <= c < 127 else "." for c in head)
        print(f"{name} head40: {printable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

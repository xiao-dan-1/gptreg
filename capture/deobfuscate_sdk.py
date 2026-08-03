#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提取 sdk.js 的字符串表 c()，把 n(数字) 调用替换为明文，产出去混淆版本。

sdk 混淆结构：function n(t,e){const r=c();return r[t-=0]}  →  n(i) = 字符串表[i]。
c() 是一个大数组字面量。解析后全局替换 n(数字)。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

SDK = Path(sys.argv[1] if len(sys.argv) > 1 else
           Path(tempfile.gettempdir()) / "openai-sentinel-demo" / "20260219f9f6" / "sdk.js")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "data/sdk_deobfuscated.js")
ROOT = Path(__file__).resolve().parent.parent


def extract_array(sdk: str, marker: str) -> str:
    start = sdk.find(marker)
    if start < 0:
        raise RuntimeError(f"marker not found: {marker}")
    bracket = start + len(marker)
    depth = 0
    i = bracket
    in_str = False
    esc = False
    while i < len(sdk):
        ch = sdk[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth < 0:  # 开头的 [ 未计深度，数组闭合 → -1
                    break
        i += 1
    # 包含开头的 [（bracket 指向 [ 后的第一个字符，需前移一位）
    return sdk[bracket - 1:i + 1]


def main() -> int:
    sdk = SDK.read_text(encoding="utf-8")
    print(f"sdk 大小: {len(sdk)}")
    # Dt() 是 _n 用到的解码器 Rn/Mt 的字符串表；c() 是 UUID 库的小表（忽略）。
    marker = "function Dt(){const t=["
    arr_text = extract_array(sdk, marker)
    print(f"Dt() 数组文本: {len(arr_text)} chars")

    # 用 node 解析 JS 数组（可能含转义/嵌套）
    tmp = ROOT / "data" / "_c_array.js"
    tmp.write_text("module.exports = " + arr_text + ";", encoding="utf-8")
    r = subprocess.run(["node", "-e", "const a=require(process.argv[1]);process.stdout.write(JSON.stringify(a));", str(tmp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        print("node 解析失败:", (r.stderr or "")[:300])
        return 1
    table = __import__("json").loads(r.stdout)
    print(f"字符串表元素数: {len(table)}")

    # 替换 Mt(N)/Rn(N) → 明文（_n 用到的解码器链）。其他链（n/Hn/Vn/Cn）不同表，不动。
    pattern = re.compile(r"(?<![.\w])(Mt|Rn)\((\d+)\)")
    hits = pattern.findall(sdk)
    print(f"找到 {len(hits)} 处 Mt/Rn(数字) 调用")
    used = set(int(i) for _, i in hits)
    oob = [i for i in used if i >= len(table)]
    if oob:
        print(f"警告: {len(oob)} 个越界索引 (如 {sorted(oob)[:10]}) — 表不完整")
    def _rep(m):
        idx = int(m.group(2))
        return table[idx] if idx < len(table) else m.group(0)
    deob = pattern.sub(_rep, sdk)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(deob, encoding="utf-8")
    print(f"去混淆版本已写: {OUT} ({len(deob)} chars)")
    print("可读性样例（前 1200 字符）:")
    print(deob[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())

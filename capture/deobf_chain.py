#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用解码链去混淆：给定表函数 + 解码器别名 + 目标函数，输出去混淆文本。

用法: python capture/deobf_chain.py <sdk.js> <table_fn> <decoder_alias> <target_fn>
例:   python capture/deobf_chain.py sdk.js pe Hn sessionObserverToken
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_array(sdk, marker):
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
                if depth < 0:
                    break
        i += 1
    return sdk[bracket - 1:i + 1]


def extract_fn(sdk, name):
    start = sdk.find(f"function {name}(")
    if start < 0:
        start = sdk.find(f"{name}=async function(")
    if start < 0:
        raise RuntimeError(f"function not found: {name}")
    i = sdk.find(")", start)
    j = sdk.find("{", i)
    depth = 0
    k = j
    in_str = False
    esc = False
    while k < len(sdk):
        ch = sdk[k]
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
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        k += 1
    return sdk[start:k + 1]


def main() -> int:
    sdk_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "openai-sentinel-demo" / "20260219f9f6" / "sdk.js"
    table_fn = sys.argv[2] if len(sys.argv) > 2 else "pe"
    alias = sys.argv[3] if len(sys.argv) > 3 else "Hn"
    target = sys.argv[4] if len(sys.argv) > 4 else "sessionObserverToken"
    sdk = sdk_path.read_text(encoding="utf-8")

    # 1) 表
    arr_text = extract_array(sdk, f"function {table_fn}(){{const t=[")
    tmp = Path(tempfile.gettempdir()) / "_tbl.js"
    tmp.write_text("module.exports = " + arr_text + ";", encoding="utf-8")
    r = subprocess.run(["node", "-e", "const a=require(process.argv[1]);process.stdout.write(JSON.stringify(a));", str(tmp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if r.returncode != 0:
        print("表解析失败:", r.stderr[:200])
        return 1
    table = json.loads(r.stdout)
    print(f"表 {table_fn}(): {len(table)} 元素")
    (Path("data") / f"{table_fn}_table.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")

    # 2) 目标函数去混淆：替换 <alias>(N) 与 <alias> 别名 t(N)（保守：只替换 alias 与 't'(紧邻 alias=)）
    def rep(m):
        idx = int(m.group(1))
        return table[idx] if idx < len(table) else m.group(0)
    fn = extract_fn(sdk, target)
    deob = re.sub(rf"(?<![.\w]){re.escape(alias)}\((\d+)\)", rep, fn)
    # 别名 t=Hn 之后的 t(N)：找 'const t=Hn' 或 'const t=pe' 形式，同作用域内 t(N)
    deob = re.sub(r"(?<![.\w])t\((\d+)\)", rep, deob)
    print(f"\n=== {target} 去混淆 ===")
    print(deob)
    return 0


if __name__ == "__main__":
    sys.exit(main())

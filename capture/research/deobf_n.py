#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提取并去混淆 _n 函数（用 Dt 表）。_n 用 Mt/Rn(解码器, 表=Dt) 与别名 t=Mt。"""
import re, sys, tempfile
from pathlib import Path

SDK = Path(sys.argv[1] if len(sys.argv) > 1 else
           Path(tempfile.gettempdir()) / "openai-sentinel-demo" / "20260219f9f6" / "sdk.js")
sdk = SDK.read_text(encoding="utf-8")

# Dt 表（已知 30 个）
TABLE = ["(((.+)+)+)+$", "fromCharCode", "resolve", "toString", "set", "match",
         "clear", "apply", "abs", "function", "get", "map", "push", "constructor",
         "parse", "charCodeAt", "log", "isArray", "stringify", "from", "scripts",
         "then", "shift", "filter", "search", "indexOf", "length", "finally",
         "bind", "catch"]

def deob_text(txt):
    # 1) 字面 Mt(N)/Rn(N)
    txt = re.sub(r"(?<![.\w])(Mt|Rn)\((\d+)\)", lambda m: TABLE[int(m.group(2))] if int(m.group(2)) < len(TABLE) else m.group(0), txt)
    # 2) 别名 t=Mt; ... t(N) —— 在 _n 内部，const t=Mt 绑定后 t(N) 即解码
    #    保守处理：只替换紧跟 't=Mt' 之后的 t(N)（同作用域），这里简单全量替换 t(N)（_n 内部 t 主要是解码器）
    txt = re.sub(r"(?<![.\w])t\((\d+)\)", lambda m: TABLE[int(m.group(1))] if int(m.group(1)) < len(TABLE) else m.group(0), txt)
    return txt

def extract_fn(sdk, name):
    start = sdk.find(f"function {name}(")
    if start < 0:
        return ""
    # 括号平衡找函数体（跳过参数列表）
    i = sdk.find(")", start)
    # 从 { 开始
    j = sdk.find("{", i)
    depth = 0
    k = j
    in_str = False
    esc = False
    while k < len(sdk):
        ch = sdk[k]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0: break
        k += 1
    return sdk[start:k + 1]

fn = extract_fn(sdk, "_n")
print("=== _n 原始长度:", len(fn), "===")
print(deob_text(fn))

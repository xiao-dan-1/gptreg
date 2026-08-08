#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模拟 turnstile 字节码程序（dx 解密后），把 88 条指令读成伪代码。

bn 基础操作（来自 _n 反混淆）: 0=On 1=XOR 2=ASSIGN 5=ADD 6=INDEX 7=CALL 8=SETREF
10=WINDOW 11=SCRIPTS 12=BN 13=CALL_IGN 14=PARSE 15=STRINGIFY 17=CALL_AWAIT 18=ATOB
19=BTOA 20=COND 21=ABSCOND 22=QUEUESWAP 23=UNDEFCHK 24=BIND 25/26/28=NOOP 27=SUB
29=LT 30=SUBRUN 33=MUL 34=PROMISERES 35=DIV
"""
import json
import sys
from pathlib import Path

BASE = {
    0: "On", 1: "XOR", 2: "ASSIGN", 3: "RESULT_CB", 4: "ERROR_CB", 5: "ADD",
    6: "INDEX", 7: "CALL", 8: "SETREF", 9: "QUEUE", 10: "WINDOW", 11: "SCRIPTS",
    12: "BN", 13: "CALL_IGN", 14: "PARSE", 15: "STRINGIFY", 16: "KEY", 17: "CALL_AWAIT",
    18: "ATOB", 19: "BTOA", 20: "COND", 21: "ABSCOND", 22: "QUEUESWAP", 23: "UNDEFCHK",
    24: "BIND", 25: "NOOP", 26: "NOOP", 27: "SUB", 28: "NOOP", 29: "LT", 30: "SUBRUN",
    33: "MUL", 34: "PROMISERES", 35: "DIV",
    # 一级程序（dx 顶层）建立的别名
    97.34: "SETREF", 10.03: "STRINGIFY", 2.28: "STRINGIFY", 16.07: "XOR", 90.17: "XOR",
    60.19: "CALL", 40.4: "CALL", 4.39: "RESULT_CB", 40.63: "ADD", 40.98: "ADD",
    29.74: "BTOA", 17.43: "BTOA", 79.03: "ABSCOND", 40.13: "ABSCOND", 84.99: "SETREF",
    56.94: "ATOB", 32.99: "ATOB", 42.27: "PARSE", 40.15: "PARSE", 85.79: "CALL_IGN",
    35.68: "CALL_IGN", 88.44: "WINDOW", 87.45: "BIND", 87.49: "ASSIGN", 92.1: "INDEX",
    16.04: "ASSIGN", 90.27: "INDEX", 19.6: "UNDEFCHK", 48.84: "UNDEFCHK", 59.39: "CALL_AWAIT",
    1.2: "CALL_AWAIT",
}

def fmt(v):
    if isinstance(v, float):
        # 整数形 float 显示为 int
        return str(int(v)) if v.is_integer() else f"{v:.2f}"
    return repr(v)

def main():
    prog = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else "data/dx_program.json").read_text(encoding="utf-8"))
    bn = {}  # key -> symbolic value（BASE 函数名 / 特殊值）
    for k, name in BASE.items():
        bn[k] = name
    out = []
    for idx, inst in enumerate(prog):
        if not isinstance(inst, list) or not inst:
            out.append(f"[{idx:2d}] <non-list: {fmt(inst)}>")
            continue
        op = inst[0]
        opname = bn.get(op, f"?{op}")
        args = inst[1:]
        # 解析参数：若参数是 bn 键，显示其符号值；若是字符串/数字常量，原样
        def resolve(a):
            if isinstance(a, (int, float)) and a in bn:
                return f"<{bn[a]}>"
            return fmt(a)
        argstr = ", ".join(resolve(a) for a in args)
        out.append(f"[{idx:2d}] {opname}({argstr})")
        # 模拟关键副作用以跟踪别名：
        # SETREF(8): bn.set(n, bn.get(e)) → 浮点键别名为目标
        if opname == "SETREF" and len(args) >= 2:
            k, e = args[0], args[1]
            if isinstance(k, (int, float)) and e in bn:
                bn[k] = bn[e]
                out.append(f"     → alias: {fmt(k)} = {bn[e]}")
        # ASSIGN(2): bn.set(n, e) 直接赋常量
        elif opname == "ASSIGN" and len(args) >= 2:
            k, e = args[0], args[1]
            if isinstance(k, (int, float)):
                bn[k] = f"V:{fmt(e)}"
                out.append(f"     → assign: {fmt(k)} = {fmt(e)}")
    print("\n".join(out))
    print()
    print("=== 浮点键最终别名表 ===")
    for k in sorted(bn):
        if k not in BASE:
            print(f"  {fmt(k)} -> {bn[k]}")

if __name__ == "__main__":
    sys.exit(main())

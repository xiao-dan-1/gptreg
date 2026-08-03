# turnstile `_n` 反混淆 + dx 字节码解密（2026-08-03 深夜）

## 结论：`_n` 是通用字节码解释器

反混淆后的 `_n`（data/deobf_n.txt）核心：
```js
const i = $(t ?? {}) ?? "";            // 从 challenge 派生 XOR 密钥
bn[set](Zt, JSON.parse(Tn(atob(n), "" + bn.get(en))));  // 解密 dx
An()...                                 // 解释执行字节码 → 产出 t
```

- **`bn` = 操作码表（VM）**：注册 30+ 操作（XOR Tn、加减乘除、JSON parse/stringify、atob/btoa、Promise 处理、window/document.scripts 读取）
- **dx 是 XOR 加密的字节码程序**：`JSON.parse(Tn(atob(dx), key))`
- **key = request_p**：`const I=new WeakMap; function D(t,n){I.set(t,n)}; function $(t){return I.get(t)}`，D(challenge, request_p) 把 request_p 存进 WeakMap → `$(challenge)` = request_p
- **这解释了 challenge 上下文绑定**：dx 用 request_p 加密，vm/browser request_p 不同 → 不能跨解密

## 解密出的字节码程序（data/dx_program.json）

- 顶层 88 条指令，如 `[97.34, 10.03, 15]`、`[16.04, 84.59, 37.07]`、`[87.49, 36.1, 'Reflect']`
- float 操作码（97.34/16.04/87.49/90.27/92.1…）= turnstile 内部引用 id
- 读取的全局：**Math/Object/Reflect/document/history/localStorage/navigator/performance/screen**
- 字符串参数：body/create/document/history/localStorage/navigator/now/null/performance/screen/set
- 含 base64 嵌套加密块（多层解密）

## 对 t 长度问题的重塑

t = 字节码程序输出 = f(dx 程序, 环境值)。vm 和浏览器跑**不同的 dx 程序**（不同 challenge），
所以「vm t 短 ~130-200 字符」**可能完全是对比混淆**（不同程序 → 不同输出长度），不是 vm 缺陷。

关键待验证：vm 用自己 challenge 跑程序，产出的 t 是否**环境值一致**（程序读 navigator/screen 等已对齐）。

## 下一步

1. 反推 opcode 分发表，把字节码程序读成伪代码（每 opcode 对应什么操作）
2. 验证 vm 执行同程序 vs 浏览器是否产生一致结果
3. 若一致 → t 有效，存活死亡来自 so/IP/行为；若不一致 → 定位环境值差异

## 工具

- `capture/deobfuscate_sdk.py` — 提取 Dt() 字符串表 + 替换 Mt/Rn(N)
- `capture/deobf_n.py` — 提取并去混淆 `_n`
- `data/deobf_n.txt` — 反混淆后的 `_n`
- `data/dx_program.json` — 解密出的字节码程序

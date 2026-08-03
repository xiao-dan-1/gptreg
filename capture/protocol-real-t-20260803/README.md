# 协议产真 t：突破记录（2026-08-03）

**结论：Node vm 跑官方 sdk.js，turnstile 求解器 `_n` 能产出真实 t（928-956 字符），无需浏览器。**

## 四个必要条件（缺一不可）

| # | 条件 | 作用 | 代码位置 |
|---|---|---|---|
| 1 | **navigator 用 `Object.defineProperty` 覆盖** | Node 内置 `globalThis.navigator` 是 getter-only，`=` 赋值静默失败 → 指纹 UA 变 `Node.js/24` → dx 解码产出畸形 JSON | installRuntime 里 navigator |
| 2 | **注册解码器密钥 `D(challenge, request_p)`** | `$ = I.get(challenge)` 返回 proof，`_n` 用它解码 dx | solve 前 `__debug_D(challenge, requestP)` |
| 3 | **setTimeout 不能同步** | 同步 setTimeout 让 `_n` 的 500ms 看门狗**立即触发** → resolve "0" | installRuntime setTimeout |
| 4 | **`_n` 的 500ms 超时提到 60s** | 解释器在 vm 里慢（~60s），默认 500ms 不够 | sdk patch `e(""+kn)}),500` → `,60000` |

## 现象链条（调试证据）

1. 最初：`_n` 返回 SyntaxError 串（`MDogU3ludGF4...`）→ dx 解码畸形（`[[8, 62.3<#...` 垃圾混入 JSON）
2. 修 navigator 后：dx 解码正常（JSON.parse 0 失败），但 `_n` 返回 `"0"`
3. 发现 `_n` 末尾：`setTimeout(()=>{e(""+kn)},500)`，kn 初始 0 → 500ms 内解释器没算完 → "0"
4. 同步 setTimeout：看门狗立即触发 → 必 "0"
5. 异步 setTimeout + 超时提到 60s：解释器跑完 → **真 t（928-956 字符）**

## 已知限制

- **慢**：每个 t 约 60s（紧贴 60s 超时，建议提超时到 120s）
- **未验证**：vm 产出的 t 是否被 create 接受、账号是否存活
- **t 解码是二进制**（694B），浏览器真 t 是 1252 字符——格式可能有细微差别，需 create 实测

## 适配器文件

- `openai_sentinel_quickjs_async.js`：**最终可用版**（异步 setTimeout + navigator fix + D 注册 + 60s 超时）
- `openai_sentinel_quickjs_navfix.js`：仅 navigator fix（中途状态）
- `openai_sentinel_quickjs_scripts.js`：navigator + document.scripts 预填（中途状态）

## 下一步

1. 把 vm 真 t 接进 `gptreg/auth.py`（新增 quickjs sentinel 源）
2. 真实 create 验证：t 是否被接受、账号存活率
3. 若存活 OK → 纯协议注册成立，可替代 browser 兜底

# turnstile widget 逆向 + 生存缺口定位（2026-08-03 深夜）

## 背景

存活判定：quickjs 全死（chatgpt.com 端点 / screen·doc 修复 / vm t+so 都无效），browser 存活。
判定逻辑假设「剩余缺口 = turnstile widget 执行（challenge-platform oneshot）」。本文件验证该假设。

## 结论：假设被证伪

**t 不是 widget 产的。** 浏览器 5 次 token() 全产出真 t（1140-1364 字符），
但捕获到 **0 个 challenge-platform 网络事件**。核心 t 完全由 sdk 内置的 `_n` 求解器
在**本地产出**，不依赖 widget。

## 证据链

| 证据 | 方法 |
|---|---|
| widget main.js = **21792B 纯 JS**（无 wasm 引用），是 bootstrap：`_cf_chl_opt` + VM 字符串解码 + oneshot POST（`/jsd/oneshot/{hash}/{nonce:ts:hmac}/{rid}`，payload `{api,c,payload}`） | 下载 data/turnstile_main.js 分析 |
| 5× token() 真 t + 0 widget 事件 | capture_widget_network.py |
| frame.html 只加载 sdk.js（30864B），无 widget | 直接导航 frame.html 捕获 |
| `_n`/solve 在 vm **无关键 UNDEF 读取**（仅 3 个无害 `document.createElement().then` 检查） | debug_undef solve 诊断 |
| challenge 上下文绑定（request_p/device_id/flow），跨注入双向产出 84B 垃圾 t | same_challenge_compare.py 双向注入 |

## 真正的缺口

vm t 恒比浏览器 t 短 ~91 字节（base64 ~100 字符），字节匹配 ~15%（保真修复后未变）。
turnstile keystream 编码的是**真实浏览器环境指纹值**：canvas 渲染、WebGL、字体、GPU、
时序等——纯 JS vm（无 canvas/WebGL 渲染）物理上无法产出这些值。

**这是结构性墙**，与好友分析的「B1/E1/D1」同级：
- 就算 `_n` 用真实 sdk 代码在 vm 里跑，keystream 依赖的真实渲染指纹仍缺
- 服务器深度校验（存活 ~1h 后）对比 keystream 与账号环境 → vm 账号必死

## 对「继续研究协议」的含义

- 逆向 widget 无用（不是 t 来源）
- 逆向 `_n` 的 keystream 生成逻辑、伪造 canvas/WebGL/字体指纹 → 技术上接近「伪造浏览器」，
  相当于自己实现一个 canvas/WebGL 渲染器喂给 keystream，难度极高且一旦细节错就全盘暴露
- 结论：纯协议注册机无法产出能存活的 turnstile keystream，browser-only 是唯一存活路径

## 交付物

- `capture/capture_widget_network.py` — widget 网络捕获（含 retry，turnstile.required 变化）
- `capture/same_challenge_compare.py` — 同 challenge 双向注入实验（证明上下文绑定）
- `data/turnstile_main.js` — 下载的 widget bootstrap
- `data/turnstile_capture/` — 捕获事件（0 widget）

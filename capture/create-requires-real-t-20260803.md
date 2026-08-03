# create 需要**非空** `t`：根因排查 + Node-VM 路线验证（2026-08-03）

## 症状

- `create_account` 一律 400 `registration_disallowed`（`type=invalid_request_error`），3 次同 body 重试全拒
- OTP 阶段（`authorize_continue`）正常，sentinel/req 200、PoW 可解
- 与 IP 地区、邮箱域名、so 有无无关

## 根因：`t` 空 vs 非空（隔离矩阵）

| create sentinel | t | so | 结果 |
|---|---|---|---|
| pow（纯 Python）| `""`（空）| xiaopp 硬编码 / 无 | ❌ 400 ×4 轮 |
| **Node VM 跑 sdk.js** | `0: SyntaxError...`（**非空**，112 字符）| 无 | ✅ 2/2 成功 |
| browser 真 Chrome | 真 t（1252 字符）| 真 so | ✅ 2/2 成功 |

**决定因素 = `openai-sentinel-token` 的 `t` 是否非空，不是是否真实。** 纯 Python pow 发 `t=""` → create 必被拒；Node 沙箱产出的 `t` 虽是 SyntaxError 错误字符串，但**非空** → create 接受。OTP 阶段 `t=""` 仍被容忍。**8 月起 OpenAI 收紧**（P1 7 月"有/无 so 双活"已过时）。

## 关键机制：Node VM + sdk.js

- **sdk.js**：OpenAI 官方反机器人脚本（`https://sentinel.openai.com/sentinel/{sv}/sdk.js`），混淆压缩，`vendor/sentinel/sdk.js` 有本地副本。设计在浏览器里采集指纹 + 算 PoW（FNV-1a）+ 调 turnstile，产出 `{p,t,c,id,flow}`。
- **Node vm**：Node 自带 `vm` 模块建 JS 沙箱。`sentinel-runner.js` 在沙箱里伪造 window/navigator/document/fetch 等浏览器 API，把 sdk.js 塞进去"真跑"（`vm.runInContext`），不启动 Chrome。
- **为什么 t 是假的**：sdk.js 的 turnstile 需要 canvas/WebAssembly/混淆 VM，Node 沙箱给不了 → 解析 challenge 的 `turnstile.dx` 时崩（SyntaxError）→ 错误串被塞进 `t`。
- 参考仓库 turb-gpt-free-register 的 `REGISTRATION_DRIVER="protocol"` 就是这个思路（Python curl_cffi + Node runner 喂 challenge）。

## Node-VM 探针记录（6 连测全假 t，但假 t 可用）

用 `--challenge-file` 喂 challenge 给 runner（本仓库 vendored / 参考仓库 runner × sdk 版本 × sentinel.openai.com / chatgpt.com 端点 × 30 flag macOS/Chrome149/jp 环境），`t` 全部是 SyntaxError 串（`MDogU3ludGF4` = base64("0: Syntax")）。**但后续实测：这个非空假 t 能过 create。**

## 修复（已实施 + 真实验收）

- `config.yaml`：`register.create_browser_fallback: true`（browser 兜底），`pow_so_source: "none"`（xiaopp 假 so 废弃）
- `gptreg/pipeline.py`：回退开启时 pow 波次只试 1 次即交棒
- `gptreg/auth.py` + `cli.py`：新增实验性 `--sentinel-source node`（Node VM 产非空 t，无浏览器）
- 验收：browser 兜底（EricWaller3362）✅；node 直连（DanielCampbell8797、ShawnaAnderson9445）✅ 均 health=ok
- 指纹自洽修复保留（`[14]=device_id`、`[0]=int`、`[16]=cores`）：与根因无关但无害

## 最终决定 / 存活实测（2026-08-03 晚）

- **默认路线：browser**（`sentinel_source: browser`）——唯一实证能存活
- **存活抽查**（注册后 ~6h）：browser 真 t 账号 MaryAbbott5178 / EricWaller3362 **ok 存活**；node 假 t 账号 DanielCampbell8797 / ShawnaAnderson9445 / CrystalKelly5814 **全被 token_invalidated 吊销**
- **假 t 必死**：SyntaxError 串（112 字符）的账号 ~6h 全死 → 假 t 只够"注册成功"，不够"活着"
- `vendor/sentinel/sdk.js` 已过时（实时版 30864B vs vendored 33806B）

## 无浏览器产真 t/so 的研究（QuickJS / any-auto-register / DanOps）

实测结论：**SDK 的 turnstile 求解器 `_n` 和 snapshot 求解器 `Nt` 在 Node vm 里都跑不出真值**：

| 方案 | t | so |
|---|---|---|
| browser（唯一可行）| 真 t（1252）| 真 so（2658）|
| node runner（sentinel-runner.js）| SyntaxError 串 | 无 |
| quickjs `__debug_n` | `"0"`（假）| — |
| quickjs `Nt(snapshot_dx)` | — | 同步 setTimeout→`session_observer_vm_timeout`；真异步→60s 后 SyntaxError 串 |

细节：
- `sessionObserverToken` = 读 `ne.get(flow).cachedSOChatReq` → `Nt(t.so.snapshot_dx)`（`Nt=Ot(()=>jt(dx))`，指令解释器）
- 状态注入可行（暴露 `ne` Map，定义时 patch `ne=new Map,ee=new Map`），但 `Nt` 解释器在 vm 里处理当前 snapshot_dx 失败
- `_n`/`Nt` 均不依赖 WASM/canvas，但解释器的 watchdog（同步 setTimeout）与指令流处理（异步）无法在 vm 兼顾
- **根本结论：当前 challenge 格式下，只有真浏览器能让 SDK 求解器正确运行。browserless 产真 t/so 不可行。**

研究遗留：
- `/tmp/qjs-test/openai_sentinel_quickjs.js`（打满补丁的适配器）、`/tmp/gpt-agreement-payment/`、`/tmp/any-auto-register/`（temp，会丢）
- 若 OpenAI 换 challenge 格式 / SDK 结构，可重新评估

## 教训

- 排障 create 失败先看 `t` 是否为空，勿先怪邮箱/IP/TLS/so
- 8 月后 "create 必须非空 t" 是硬门槛；OTP 仍可纯 pow
- Node 假 t 是"即时成功率"换"长期质量"——跑量可，惜号勿用

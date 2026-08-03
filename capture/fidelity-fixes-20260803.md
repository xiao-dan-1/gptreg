# quickjs 保真修复：对照真浏览器地面真值逐字段对齐（2026-08-03 深夜）

## 目标

基于**当前 sdk** 的真浏览器 p 数组（fresh harvest `browser-so-harvest-20260803-222139`），
逐字段对齐 vm 输出。此前用的 July 12 真值是旧 sdk，已弃用。

## 关键发现

| 发现 | 意义 |
|---|---|
| backend-api sdk.js 只有 **923B**，是引导加载器：定义 `__sentinel_token_pending`/`__sentinel_init_pending`/`SentinelSDK` stub，再动态加载 `sentinel/20260219f9f6/sdk.js` | **我们的版本 20260219f9f6 就是当前真实版本**；`__sentinel_*`/`SentinelSDK` 是**真浏览器全局**（非污染） |
| p[10] 机制（sdk 反混淆）：`T(){const t=R(Object.keys(Object.getPrototypeOf(navigator)));return t+"−"+navigator[t].toString()}` | 采样 **Navigator 原型** 的键 → 我们 vm 的 navigator 原型是 Object.prototype（空 keys）→ 恒 `'undefined'` |
| [3]/[9]/[14] 真浏览器同样漂移（25/43/94、2/3/2、每次新 UUID） | 好友 A2 的「漂移=非人」**部分被真值平反** |
| [0]=3000、[5]=backend-api 加载器 URL、[13]=5-25s 页面耗时 | 都是真值 |

## 修复清单（已验证，probe 复跑对比）

1. **solve 复用同一份 fp（头号 bug）**：原 solve payload 只带 device_id/request_p/challenge/flow，
   installRuntime 全用默认指纹（UA=`Mozilla/5.0`、屏幕 1366x768、memory 8、time_origin 固定值）。
   同注册的 requirements/solve 指纹不一致 → 已改为 `solve_payload = dict(fp) + {request_p,challenge,flow}`。
2. **time_origin 会话固定**（A3）：fp 一次注册算一次，两动作复用同一值；`performance.now() = Date.now() - timeOrigin`。
3. **隐藏 vm/诊断全局**：`__debugP`/`__debug_D`/`__debug_se`/`__payload_json`/`__sdk_source`/`__vm_*`
   改 non-enumerable（真浏览器没有）。`__sentinel_*`/`SentinelSDK` **保持可枚举**（真加载器创建）。
4. **Node 专属全局隐藏枚举**：`process`/`Buffer`/`global`（保留值，仅不可枚举；否则 wrapper 的 stdout 挂）。
5. **navigator 补 Navigator.prototype 候选键**：login/keyboard/vendor/getInterestGroupAdAuctionData/… 放原型，
   Symbol.toStringTag 给真实类名 → p[10] 从 `'undefined'` 变为真实 `name−value`（实测 `login−[object NavigatorLogin]`，与真浏览器同款）。
6. **plugins/mimeTypes/vendorSub**（A1）：typeof 从 undefined → object/string。
7. **window 尺寸/DPR**：innerWidth/outerHeight/devicePixelRatio。
8. **document.scripts/currentScript = backend-api 加载器** → p[5] 稳定 = `backend-api/sentinel/sdk.js`。
9. **page_elapsed_ms 3-15s 偏移** → p[13] 量级真实（真浏览器 5-25s）。

## 修复后 vs 真浏览器（当前 sdk）

[0][2][5][6][7][10][11][12][13][14][17][18-24] ✅ 全部匹配；[3][9] 同真浏览器漂移。
剩余差异：
- p[1] 时区**名中文**（Node ICU 跟随系统中文，LANG 无效）——offset/格式正确，仅本地化名不同，已知限制。
- p[4]/[8]/[16]（UA/languages/cores）取决于 config.yaml，probe 用空 config 才显示裸值。

## 交付物

- `gptreg/sentinel_quickjs.py` — fp 一次算 + solve 复用 + TZ/page_elapsed/sdk_url
- `vendor/sentinel/openai_sentinel_quickjs.js` — installRuntime 全部保真改动 + 诊断钩子（debug_undef）
- `capture/probe_quickjs_consistency.py` — 复跑探针（含 uuid_calls/fp_reads 诊断）
- `capture/browser-so-harvest-20260803-222139/` — 当前 sdk 真值
